import os
import re
import io

from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice
from discord_slash import SlashCommandOptionType


from lib.tinyConnector import TinyConnector
from lib.data import Incident, Driver, State


from util.verboseErrors import VerboseErrors
from util.interaction import ack_message, guess_target_section, guess_target_text, get_client_response, get_client_reaction, wait_confirm_deny

from util.displayEmbeds import incident_embed
from util.htm_gen import gen_html_report


class IncidentModule(commands.Cog):

    INC_HELP   = '```*incident <command>\n'\
                 '\t[@mention] - open a new ticket\n'\
                 '\tcancel     - abort an opened ticket (do not use for normal ticket closing)\n'\
                 '\tsetup      - set some properties (use \'incident setup help\')```'

    SETUP_HELP = '```*incident setup <command>\n'\
                 '\tcategory - set the category where channels are created\n'\
                 '\tstewards - set the steward role\n'\
                 '\tsummary  - set the summary channel\n'\
                 '\tlog      - set the log channel\n'\
                 '\thelp     - show this message```'


    # =====================
    # internal functions
    # =====================
    def __init__(self, client):
        self.client = client
        self.incident_timeout.start()


    def _is_member_steward(self, member, steward_id):
            return any(r.id == steward_id for r in member.roles)


    async def _del_msg_list(self, channel, msg_ids: []):
        for m_id in msg_ids:
            try:
                msg = await channel.fetch_message(m_id)
                await msg.delete()
            except:
                # ignore if msg is not existing
                pass


    async def _test_msg_was_send(self, channel, tgt_author_id, bot_id):

        # this iterates in reverse
        # limit detection to last 5 messages (reducing latency cmp. to last 10 or 20)
        async for message in channel.history(limit=5):
            if message.author.id == bot_id:
                return False

            elif message.author.id == tgt_author_id:
                return True

        # the author did not send a message
        return False




    #################################################
    ## Cancel an active ticket - if permissions avail
    #################################################

    async def cancel(self, cmd):

        server = TinyConnector.get_guild(cmd.guild.id)
        incident = server.active_incidents.get(cmd.channel.id, None)


        if not incident:
            await cmd.send('This command only works in an active incident channel')
            return


        if cmd.author.id != incident.victim.u_id and not self._is_member_steward(cmd.author, server.stewards_id):
            await cmd.send('You are not authorized to cancel this ticket')
            return


        # the victim cannot cancel the ticket anymore after completing the process
        if cmd.author.id == incident.victim.u_id and incident.state.value >= State.OFFENDER_STATEMENT.value:
            await cmd.send('You cannot cancel this ticket anymore')
            return


        # the stewards can always cancel a ticket

        await cmd.send('This incident is now marked as closed. It will be deleted soon.')
        incident.state = State.CLOSED_PHASE
        incident.locked_time = datetime.now().timestamp()

        # the incident channel is the command channel
        await cmd.channel.edit(name= '❌ ' + cmd.channel.name[1:])


        TinyConnector.update_incident(cmd.guild.id, incident)



    #################################################
    ## Incident Creation - Initial question on victim
    #################################################


    async def _set_offender_write(self, channel, victim, offender):
        await channel.set_permissions(victim, read_messages=True, send_messages=False, read_message_history=True)
        await channel.set_permissions(offender, read_messages=True, send_messages=True, read_message_history=True)


    async def _set_victim_write(self, channel, victim, offender):
        await channel.set_permissions(victim, read_messages=True, send_messages=True, read_message_history=True)
        await channel.set_permissions(offender, read_messages=True, send_messages=False, read_message_history=True)

    async def _set_all_write(self, channel, victim, offender):
        await channel.set_permissions(victim, read_messages=True, send_messages=True, read_message_history=True)
        await channel.set_permissions(offender, read_messages=True, send_messages=True, read_message_history=True)


    async def _set_no_write(self, channel, victim, offender):
        await channel.set_permissions(victim, read_messages=True, send_messages=False, read_message_history=True)
        await channel.set_permissions(offender, read_messages=True, send_messages=False, read_message_history=True)



    async def incident(self, cmd, offender_id: int):

        perms = discord.Permissions()
        perms.manage_channels = True
        perms.send_messages = True
        perms.read_message_history = True
        perms.add_reactions = True
        perms.manage_permissions = True
        perms.embed_links = True


        server = TinyConnector.get_guild(cmd.guild.id)

        if not server.incident_section_id:
            await cmd.send('You need to setup a section for incidents first. Please ask an admin to use `{:s}incident setup`'.format(server.prefix))
            return

        if not server.statement_ch_id:
            await cmd.send('You need to setup a summary channel for incidents first. Please ask an admin to use `{:s}incident setup`'.format(server.prefix))
            return

        if not server.stewards_id:
            await cmd.send('No steward role is specified. Please ask an admin to use `{:s}incident setup'.format(server.prefix))
            return


        section = self.client.get_channel(server.incident_section_id)

        if not await VerboseErrors.show_missing_perms("incident", perms, section, channel_overwrite=True, text_alternative=cmd.channel):
            return


        dm = await cmd.author.create_dm()


        # send the disclaimer at this position
        # this is used to probe for DM permission
        if os.path.exists('modules/disclaimer.txt'):
            with open('modules/disclaimer.txt', 'r') as f:
                disclaimer_str = ' '.join(f.readlines())

        try:
            await dm.send(disclaimer_str)
        except discord.errors.Forbidden as e:
            embed = discord.Embed(title='Missing DM Permission',
                                    description='Please [change your preferences]({:s}) and invoke this '\
                                                'command again.\n You can revert the changes after the ticket is setup.'
                                                .format(r'https://support.discord.com/hc/en-us/articles/217916488-Blocking-Privacy-Settings-'),
                                    color=0xff0000)
            await cmd.send(embed = embed)
            return


        # channel is visible for all
        # cmd.send is not
        await cmd.send('I have sent you a DM to continue the ticket process.')
        await cmd.channel.send('This incident is now looked at by the stewards. There\'s no need for further discussions in this channel.')

        if dm is None:
            return

        incident = await self.incident_setup_dm(cmd, dm)
        if incident is None:
            return


        incident.offender.u_id = offender_id
        incident = await self.incident_setup_channel(cmd, incident)


        # re-fetch not required, as incident object is newly created
        TinyConnector.incr_inc_cnt(server)
        TinyConnector.update_incident(server.g_id, incident)



    async def incident_flow_raw(self, cmd, dm):
        """This holds the answer-response flow of setting up the incident with its starting information
           There's no further user-information or summup etc

        Returns:
            [type]: [the created incident, might hold None fields, but is never None itself]
        """
        incident = Incident()
        incident.victim = Driver()
        incident.offender = Driver()

        incident.victim.u_id = cmd.author.id


        q = await dm.send(embed=discord.Embed(title='State the Game- and Race-name'))
        r = await get_client_response(self.client, q, 600, cmd.author)

        if r is None:
            return None

        incident.race_name = r

        q = await dm.send(embed=discord.Embed(title='State your drivers in-game name'))
        r = await get_client_response(self.client, q, 600, cmd.author)

        if r is None:
            return None
        incident.victim.name = r

        q = await dm.send(embed=discord.Embed(title='State your car number', description='Only digits are allowed'))
        r = await get_client_response(self.client, q, 600, cmd.author, lambda x: x.isdigit())

        if r is None:
            return None
        incident.victim.number = int(r)


        q = await dm.send(embed=discord.Embed(title='State the other drivers name'))
        r = await get_client_response(self.client, q, 600, cmd.author)

        if r is None:
            return None
        incident.offender.name = r

        q = await dm.send(embed=discord.Embed(title='State the other drivers number', description='Only digits are allowed'))
        r = await get_client_response(self.client, q, 600, cmd.author, lambda x: x.isdigit())

        if r is None:
            return None
        incident.offender.number = int(r)


        q = await dm.send(embed=discord.Embed(title='State the incident classification', description='according to [rule 4.1](https://docs.google.com/document/d/1VJUtz6EFFXpEP-VS2--kDzZks8YcBrUL8xs4Bm9smVk/edit#bookmark=id.r2jbeek7bvkc)'))
        r = await get_client_response(self.client, q, 600, cmd.author)

        if r is None:
            return None
        incident.infringement = r


        q = await dm.send(embed=discord.Embed(title='If possible, state the race lap and corner', description='\'-\' if unspecified'))
        r = await get_client_response(self.client, q, 600, cmd.author)

        if r is None:
            return None
        incident.lap = r

        return incident



    async def incident_setup_dm(self, cmd, dm):


        embed = discord.Embed(title='New Incident Ticket',
                              description='Please answer the following questions and ONLY the following questions.\n'\
                                            'At the end of this process you\'ll get the chance to review or cancel the ticket.\n'\
                                            'You do not need to create a new ticket if you made a typo.')

        await dm.send(embed=embed)



        incident = await self.incident_flow_raw(cmd, dm)

        if incident is None:
            await dm.send('Ticket cancelled. You didn\'t fill in the answers fast enough. You can invoke the command again to start a new ticket.')
            return None


        await dm.send('Here\'s a summary of the incident. Please confirm ✅ or cancel ❌ the ticket.')
        await dm.send('You can edit the ticket by reacting with 🔧')
        embed_msg = await dm.send(embed=incident_embed(incident, "Incident details", incident.race_name))


        abort_loop = False
        while not abort_loop:

            reaction = await get_client_reaction(self.client, embed_msg, 600, cmd.author, ['✅', '❌', '🔧'])

            # give the author a second chance
            if reaction is None:
                await dm.send('If you do not confirm this ticket, it will be aborted in 10 minutes.')
                reaction = await get_client_reaction(self.client, embed_msg, 600, cmd.author, ['✅', '❌', '🔧'])


            if reaction is None or reaction == '❌':
                await dm.send('Cancelling ticket. You can start again by reinvoking the command.')
                incident = None
                abort_loop = True
            elif reaction == '🔧':
                await dm.send('Correcting incident fields. You can copy-paste the fields you do not want to change')
                incident = await self.incident_flow_raw(cmd, dm)
                embed_msg = await dm.send(embed=incident_embed(incident, "Event details", incident.race_name))
            elif reaction == '✅':
                await dm.send('You completed the ticket initialization. Please head back to the server to enter further incident details.')
                await dm.send('I tagged you in the appropriate incident channel.')
                abort_loop = True


        # may be None if it was cancelled
        return incident



    async def incident_setup_channel(self, cmd, incident):

        # need update for incident number, otherwise concurrent access might break sequential numbering
        server = TinyConnector.get_guild(cmd.guild.id)

        # create channel and ask user for more input
        ch_name = '🅰 Incident Ticket - {:d}'.format(server.incident_cnt + 1)

        section = self.client.get_channel(server.incident_section_id)
        steward_role = cmd.guild.get_role(server.stewards_id)

        offender = await cmd.guild.fetch_member(incident.offender.u_id)

        if section is None:
            await cmd.send('Failed to create a channel, please ask an admin to re-set the category with `{:s}incident setup`'.format(server.prefix))
            return


        inc_channel = await cmd.guild.create_text_channel(ch_name, category=section)
        incident.channel_id = inc_channel.id


        # ===================================
        # THO ORDER OF THIS IS VERY IMPORTANT
        # @EVERYONE NEEDS TO BE SET LAST
        # ===================================

        try:
            await inc_channel.set_permissions(cmd.guild.me, manage_messages=True, read_messages=True, send_messages=True, read_message_history=True)
        except Exception as e:
            print('bot permissions:')
            print(e)

        try:
            await inc_channel.set_permissions(steward_role, read_messages=True, send_messages=True, read_message_history=True)
        except Exception as e:
            print('steward permission:')
            print(e)

        await self._set_victim_write(inc_channel, cmd.message.author, offender)

        try:
            await inc_channel.set_permissions(cmd.guild.default_role, read_messages=False, send_messages=False, read_message_history=False)
        except Exception as e:
            print('everyone permissions:')
            print(e)



        # ask the initial question, from then on, handling is done in events

        embed_msg = await inc_channel.send(embed=incident_embed(incident, ch_name[2:], incident.race_name))
        await embed_msg.add_reaction('⏩')  # skip forward emoji


        req1 = await inc_channel.send('{:s} Please take 1 or 2 paragraphs to state what happened, what effect it had on your race and '\
                                'why you think its a punishable behaviour (do not post links to footage yet)'.format(cmd.author.mention))


        req2 = await inc_channel.send('React with ⏩ once you stated all points') # skip forwad emoji

        incident.cleanup_queue.append(req1.id)
        incident.cleanup_queue.append(req2.id)

        return incident





    #################################################
    ##  Incident Messages - send user messages
    #################################################





    async def incident_victim_proof(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        incident.state  = State.VICTIM_PROOF


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        # delete the old questions, this helps in keeping the channel clean
        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        m1 = await channel.send('<@{:d}> Please upload the proof of the incident\n'.format(incident.victim.u_id))

        m3 = await channel.send('If you do not add any proof, this ticket might be closed without further notice.')

        embed = discord.Embed(title='Recommended Upload Solutions')
        embed.add_field(name='Streamable', value = '[choose for fast and simple upload](https://streamable.com)', inline=False)
        embed.add_field(name='Youtube', value = '[choose for more control over your upload](https://youtube.com)', inline=False)

        m4 = await channel.send(embed=embed)

        # skip forwad emoji
        msg = await channel.send('React with ⏩ once you added all proof')
        await msg.add_reaction('⏩')


        incident.cleanup_queue.extend([m1.id, m3.id, m4.id, msg.id])

        TinyConnector.update_incident(server.g_id, incident)



    async def incident_notify_offender(self, guild, channel_id, incident_id, check_proof_exists):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        victim = await guild.fetch_member(incident.victim.u_id)
        offender = await guild.fetch_member(incident.offender.u_id)

        # do not change state-machine yet
        # next step requires a valid offender-id to be entered
        # if this fails, the state machine remains in the previous state


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)

        if check_proof_exists:
            if not await self._test_msg_was_send(channel, incident.victim.u_id, self.client.user.id):

                m1 = await channel.send('Are you sure you don\'t want to add any proof?\nThe incident might get cancelled if no sufficient evidence is provided.')
                m2 = await channel.send('If you decide to not add further evidence, confirm this with a text message (e.g. `no evidence required`) and react again.')
                await m2.add_reaction('⏩')

                incident.cleanup_queue.extend([m1.id, m2.id])
                TinyConnector.update_incident(server.g_id, incident)
                return


        # delete the old questions, this helps in keeping the channel clean
        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []

        # fake a text message
        # this will reset the state-machine watchdog
        # otherwise it would trigger this message at each iteration, after it was aborted once
        t = datetime.now()
        incident.last_msg = t.timestamp()


        # save is required here, as next steps will require delay
        TinyConnector.update_incident(server.g_id, incident)


        # # re-fetch the server-object, as it could have changed
        # server = TinyConnector.get_guild(guild.id)
        # incident = server.active_incidents[incident_id]


        if incident.state == State.CLOSED_PHASE:
            # the incident was closed in the mean time
            return
        else:
            # the validator guarantees a return with valid id
            offender_id = incident.offender.u_id

            q2 = await channel.send('<@{:d}> Please state your point of view and any other comments you want to add'.format(offender_id))
            # skip forwad emoji
            msg = await channel.send('React with ⏩ once you stated your points')
            await msg.add_reaction('⏪')
            await msg.add_reaction('⏩')

            # incr. state-machine on successfull offender-determination
            incident.state = State.OFFENDER_STATEMENT

            await self._set_offender_write(channel, victim, offender)


            incident.cleanup_queue.extend([msg.id, q2.id])


        TinyConnector.update_incident(server.g_id, incident)

        await channel.edit(name='🅾 ' + channel.name[1:])




    async def incident_offender_proof(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        incident.state = State.OFFENDER_PROOF


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        q1 = await channel.send('<@{:d}> upload additional proof if you have some\n'.format(incident.offender.u_id))

        embed = discord.Embed(title='Recommended Upload Solutions')
        embed.add_field(name='Streamable', value = '[choose for fast and simple upload](https://streamable.com)', inline=False)
        embed.add_field(name='Youtube', value = '[choose for more control over your upload](https://youtube.com)', inline=False)

        q2 = await channel.send(embed=embed)

        # skip forwad emoji
        msg = await channel.send('React with ⏩ once you added all proof')
        await msg.add_reaction('⏩')


        incident.cleanup_queue.extend([q1.id, q2.id, msg.id])
        TinyConnector.update_incident(server.g_id, incident)




    async def incident_notify_stewards(self, guild, channel_id, incident_id, check_proof_exists):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]



        victim = await guild.fetch_member(incident.victim.u_id)
        offender = await guild.fetch_member(incident.offender.u_id)

        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)



        if check_proof_exists:
            if not await self._test_msg_was_send(channel, incident.offender.u_id, self.client.user.id):
                m1 = await channel.send('Are you sure you don\'t want to add any proof?')
                m2 = await channel.send('If all important footage is already posted, confirm this with a text message (e.g. `no further evidence`) and react  again.')
                await m2.add_reaction('⏩')

                incident.cleanup_queue.extend([m1.id, m2.id])
                TinyConnector.update_incident(server.g_id, incident)
                return





        # only advance if proof check passes
        incident.state = State.STEWARD_STATEMENT


        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        await self._set_no_write(channel, victim, offender)


        # the poll turns out to be useless, if the incident channels are private
        """
        embed = discord.Embed(title='Poll', description='Vote if you think this incident should be punished or not')
        embed.set_footer(text='This is only to get a general mood of the stewards. The decision is in no way bound by this poll')
        embed_msg = await channel.send(embed=embed)
        await embed_msg.add_reaction('✅')
        await embed_msg.add_reaction('❌')
        """


        q1 = await channel.send('<@&{:d}> please have a look at this incident and state your judgement.'.format(server.stewards_id))
        msg = await channel.send('React with ⏩ once the final steward statement is issued. You can allow both parties to respond to your statement')

        await msg.add_reaction('⏪')
        await msg.add_reaction('⏩')


        incident.cleanup_queue.extend([q1.id, msg.id])
        TinyConnector.update_incident(server.g_id, incident)

        await channel.edit(name = '🛂 ' + channel.name[1:])




    async def incident_steward_sumup(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # incident.state += 1


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        TinyConnector.update_incident(server.g_id, incident)


        q1 = await channel.send('<@&{:d}> please state the type of this incident (driver reported as `{:s}`)'.format(server.stewards_id, incident.infringement))
        category = await get_client_response(self.client, q1, 300)

        q2 = await channel.send('<@&{:d}> please state the outcome (and resulting penalty)'.format(server.stewards_id))
        outcome = await get_client_response(self.client, q2, 300)


        # re-fetch, as db could have changed
        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # even assign on None
        incident.outcome = outcome
        incident.infringement = category

        incident.cleanup_queue.extend([q1.id, q2.id])

        TinyConnector.update_incident(server.g_id, incident)




    async def incident_steward_end_statement(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        incident.state = State.DISCUSSION_PHASE

        victim = await guild.fetch_member(incident.victim.u_id)
        offender = await guild.fetch_member(incident.offender.u_id)


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        TinyConnector.update_incident(server.g_id, incident)


        msg = await channel.send('<@&{:d}> you can close (🔒)  the incident at any point or modify the outcome (🔧) by reacting to it.'.format(server.stewards_id))


        eb = await channel.send(embed=incident_embed(incident, channel.name[2:], incident.race_name))
        await eb.add_reaction('🔒')
        await eb.add_reaction('🔧')

        await channel.send('<@!{:d}>, <@!{:d}> please review the stewards statement. '\
                            'You can respond to the judgement until the incident is locked by a steward.'
                            .format(incident.victim.u_id, incident.offender.u_id))


        await self._set_all_write(channel, victim, offender)


        await channel.edit(name = '✅ ' + channel.name[1:])



    async def incident_modify_outcome(self, guild, channel_id, incident_id, editing_steward):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)



        await channel.send('{:s} please watch your DMs to modify this ticket'.format(editing_steward.mention))

        dm = await editing_steward.create_dm()
        await dm.send(embed=incident_embed(incident, channel.name[2:], incident.race_name))


        await dm.send('Here you can correct the infringement and outcome of the ticket. Ignore this messages if you reacted by accident.\n')


        q1 = await dm.send('Please correct the infringement (type `-` if it didn\'t change)')
        infringement = await get_client_response(self.client, q1, 300, editing_steward)

        q1 = await dm.send('Please correct the outcome (type `-` if it didn\'t change)')
        outcome = await get_client_response(self.client, q1, 300, editing_steward)



        # re-fetch, as db could have changed
        # only assign if outcome has changed
        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]
        is_modified = False

        if infringement and infringement != '-':
            incident.infringement = infringement
            is_modified = True

        if outcome and outcome != '-':
            incident.outcome = outcome
            is_modified = True


        if is_modified:
            TinyConnector.update_incident(server.g_id, incident)

            await dm.send('Done')
            eb = await channel.send(embed=incident_embed(incident, channel.name[2:], incident.race_name))
            await eb.add_reaction('🔒')
            await eb.add_reaction('🔧')
        else:
            await dm.send('No modification performed.')




    async def incident_close_incident(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        incident.state = State.CLOSED_PHASE
        incident.locked_time = datetime.now().timestamp()

        TinyConnector.update_incident(guild.id, incident)


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        if server.statement_ch_id:
            statement_ch = guild.get_channel(server.statement_ch_id)
            await statement_ch.send(embed = incident_embed(incident, channel.name[2:], incident.race_name))


        await channel.send('The ticket is closed, please do not interact with this channel anymore.')


        if server.log_ch_id:
            log_ch = guild.get_channel(server.log_ch_id)

            # post the report summary in the incident channel, until the design is improved
            html_str = await gen_html_report(channel, incident.victim.u_id, incident.offender.u_id, server.stewards_id, self.client.user.id)

            if html_str:
                f_p = io.StringIO(html_str)
                await log_ch.send(file=discord.File(fp=f_p, filename=channel.name[2:] + '.html'))


        await channel.edit(name= '🔒 ' + channel.name[1:])
        print('renamed channel to locked state')




    async def incident_delete(self, guild, incident_id):
        """deletes the incident from the db
           deletes the incident channel

        Args:
            guild ([type]): discord guild object
            incident_id ([type]): id of the incident (dict key)
        """

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        TinyConnector.delete_incident(server.g_id, incident_id)
        # silent fail?
        await channel.delete()




    async def revert_incident_state(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)
        victim = await guild.fetch_member(incident.victim.u_id)
        offender = await guild.fetch_member(incident.offender.u_id)

        if incident.state == State.VICTIM_STATEMENT:
            await channel.send('You cannot revert this ticket any further.'\
                               ' Try cancelling the ticket (`incident cancel`).')
            return


        if incident.state == State.VICTIM_PROOF:
            # there's no fixed method for this question
            # permissions do not change between states V_PROOF and V_STATEMENT

            req1 = await channel.send('Please take 1 or 2 paragraphs to state what happened, what effect it had on your race and '\
                                'why you think its a punishable behaviour (do not post links to footage yet)')


            req2 = await channel.send('React with ⏩ once you stated all points') # skip forwad emoji

            incident.cleanup_queue.append(req1.id)
            incident.cleanup_queue.append(req2.id)
            incident.state = State.VICTIM_STATEMENT
            TinyConnector.update_incident(guild.id, incident)

            await self._set_victim_write(channel, victim, offender)

        elif incident.state == State.OFFENDER_STATEMENT:
            await self.incident_victim_proof(guild, channel.id, incident.channel_id)
            await self._set_victim_write(channel, victim, offender)

        elif incident.state == State.OFFENDER_PROOF:
            await self.incident_notify_offender(guild, channel.id, incident.channel_id, check_proof_exists=False)
            await self._set_offender_write(channel, victim, offender)

        elif incident.state == State.STEWARD_STATEMENT:
            await self.incident_offender_proof(guild, channel.id, incident.channel_id)
            await self._set_offender_write(channel, victim, offender)

        elif incident.state == State.DISCUSSION_PHASE:
            await self.incident_notify_stewards(guild, channel.id, incident.channel_id, check_proof_exists=False)
            await self._set_no_write(channel, victim, offender)

        elif incident.state == State.CLOSED_PHASE:
            await self.incident_steward_end_statement(guild, channel.id, incident.channel_id)
            await self._set_all_write(channel, victim, offender)


    # =====================
    # events functions
    # =====================

    @commands.Cog.listener()
    async def on_ready(self):
        print('IncidentModule loaded')



    @commands.Cog.listener()
    async def on_message(self, message):

        # explicitly count own messages

        if not message.guild:
            return

        server = TinyConnector.get_guild(message.guild.id)

        # check if this channels hosts an event
        incident = server.active_incidents.get(message.channel.id, None)

        if not incident:
            return


        if message.content and message.content == '⏩':
            m = await message.channel.send('In order to advance the ticket, you need to *react* with ⏩ instead of sending a message')
            await m.add_reaction('⏩')
            incident.cleanup_queue.append(m.id)



        # timeout counter for closing the incident
        t = datetime.now()
        incident.last_msg = t.timestamp()

        TinyConnector.update_incident(server.g_id, incident)





    @tasks.loop(minutes=5)
    async def incident_timeout(self):
        t = datetime.now()

        for guild in self.client.guilds:
            server = TinyConnector.get_guild(guild.id)



            for inc_key in server.active_incidents:
                incident = server.active_incidents[inc_key]

                channel = guild.get_channel(incident.channel_id)

                last_msg = datetime.fromtimestamp(incident.last_msg)

                delta = t - last_msg


                if channel is None:
                    # don't immediately delete incident if channel is not existing
                    # discord gateway could be down instead
                    # TODO: decide later what to do
                    continue


                if incident.state == State.VICTIM_STATEMENT and delta > timedelta(minutes=30):
                    await self.incident_victim_proof(guild, channel.id, incident.channel_id)

                elif incident.state == State.VICTIM_PROOF and delta > timedelta(minutes=30):
                    # if this fails, the state-machine will not advance
                    # this will lead to continuous pinging of the victim (by design)
                    await self.incident_notify_offender(guild, channel.id, incident.channel_id, check_proof_exists=False)

                # offender got 2 day for initial statement
                elif incident.state == State.OFFENDER_STATEMENT and delta > timedelta(days=1):
                    await self.incident_offender_proof(guild, channel.id, incident.channel_id)

                # further 2 hours for upload of proof
                elif incident.state == State.OFFENDER_PROOF and delta > timedelta(hours=2):
                    await self.incident_notify_stewards(guild, channel.id, incident.channel_id, check_proof_exists=False)

                # the stewards got 2 days of reaction
                elif incident.state == State.STEWARD_STATEMENT and delta > timedelta(days=5):
                    await self.incident_steward_sumup(guild, channel.id, incident.channel_id)
                    await self.incident_steward_end_statement(guild, channel.id, incident.channel_id)


                # the incident is auto-closed after 1 further day
                elif incident.state == State.DISCUSSION_PHASE and delta > timedelta(days=2):
                    await self.incident_close_incident(guild, channel.id, incident.channel_id)


                # state 7 is closed incident with no further interaction
                # it will be deleted after a certain timedelta
                elif incident.state == State.CLOSED_PHASE:
                    # channel is deleted after 2 more days (for record)
                    delta = t - datetime.fromtimestamp(incident.locked_time)
                    if delta > timedelta(days=2):
                        await self.incident_delete(guild, inc_key)


    @incident_timeout.before_loop
    async def before_incident_timeout(self):
        print('waiting...')
        await self.client.wait_until_ready()
        print('done')


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):

        if payload.user_id == self.client.user.id:
            return

        if payload.guild_id is None:
            return  # Reaction is on a private message

        guild = self.client.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        author_id = payload.member.id

        if not message:
            return

        server = TinyConnector.get_guild(guild.id)

        # check if this channels hosts an event
        incident = server.active_incidents.get(message.channel.id, None)

        if not incident:
            return


        # the state machine cannot be shared for timeout, as the trigger conditions are different

        if payload.emoji.name == '⏩':
            # advance over state-machine
            # increment the incident state before call, as async method could delay and lead to altered database
            if incident.state == State.VICTIM_STATEMENT and payload.member.id == incident.victim.u_id:
                await self.incident_victim_proof(guild, channel.id, incident.channel_id)

            elif incident.state == State.VICTIM_PROOF and payload.member.id == incident.victim.u_id:
                await self.incident_notify_offender(guild, channel.id, incident.channel_id, check_proof_exists=True)

            elif incident.state == State.OFFENDER_STATEMENT and payload.member.id == incident.offender.u_id:
                await self.incident_offender_proof(guild, channel.id, incident.channel_id)

            elif incident.state == State.OFFENDER_PROOF and payload.member.id == incident.offender.u_id:
                await self.incident_notify_stewards(guild, channel.id, incident.channel_id, check_proof_exists=True)

            elif incident.state == State.STEWARD_STATEMENT and self._is_member_steward(payload.member, server.stewards_id):
                await self.incident_steward_sumup(guild, channel.id, incident.channel_id)
                await self.incident_steward_end_statement(guild, channel.id, incident.channel_id)


        elif payload.emoji.name == '🔒':
            if incident.state == State.DISCUSSION_PHASE and self._is_member_steward(payload.member, server.stewards_id):
                await self.incident_close_incident(guild, channel.id, incident.channel_id)

        elif payload.emoji.name == '🔧':
            if incident.state == State.DISCUSSION_PHASE and self._is_member_steward(payload.member, server.stewards_id):
                await self.incident_modify_outcome(guild, channel.id, incident.channel_id, payload.member)

        elif payload.emoji.name == '⏪':
            if self._is_member_steward(payload.member, server.stewards_id):
                await self.revert_incident_state(guild, channel.id, incident.channel_id)
            else:
                await channel.send('Only stewards can revert the ticket state')


        # state 7 is closed incident with no further interaction
        # it will be deleted after a certain timedelta



    # =====================
    # commands functions
    # =====================

    @cog_ext.cog_subcommand(base='incident', name='report', description='open a new incident report against another driver',
                             options=[
                                create_option(
                                    name="offender",
                                    description='the driver causing the incident',
                                    option_type=SlashCommandOptionType.USER,
                                    required=True,

                                )
                            ])
    async def incident_report(self, ctx: SlashContext, offender):
        await self.incident(ctx, offender.id)


    @cog_ext.cog_subcommand(base='incident', name='cancel', description='cancel an active incident')
    async def incident_cancel(self, ctx: SlashContext):
        await self.cancel(ctx)



    @cog_ext.cog_subcommand(base='incident', subcommand_group='setup', name='roles', description='setup the incident roles (admin)',
                            options=[
                                create_option(
                                    name='mode',
                                    description='select the operation mode',
                                    required=True,
                                    option_type=SlashCommandOptionType.STRING,
                                    choices=[
                                        create_choice(
                                            name='steward role',
                                            value='steward'
                                        )
                                    ]

                                ),
                                create_option(
                                    name='role',
                                    description='the mention of the role to set',
                                    required=True,
                                    option_type=SlashCommandOptionType.ROLE
                                )
                            ])
    async def incident_setup_steward(self, ctx: SlashContext, mode, role):

        if not ctx.author.guild_permissions.administrator:
            await ctx.send('You do not have permissions to execute this command')
            return

        if mode == 'steward':
            server = TinyConnector.get_guild(ctx.guild.id)
            server.stewards_id = int(role.id)
            TinyConnector.update_guild(server)

            await ctx.send(f'New steward role is {role.mention}')




    @cog_ext.cog_subcommand(base='incident', subcommand_group='setup', name='channels', description='setup tho incident channels (admin)',
                                options=[
                                    create_option(
                                        name='mode',
                                        description='set the operation mode',
                                        required=True,
                                        option_type=SlashCommandOptionType.STRING,
                                        choices=[
                                            create_choice(
                                                name='ticket category',
                                                value='category'
                                            ),
                                            create_choice(
                                                name='summary channel',
                                                value='summary'
                                            ),
                                            create_choice(
                                                name='log channel',
                                                value='log'
                                            )
                                        ]
                                    ),
                                    create_option(
                                        name='channel',
                                        description='the channel of the selected mode',
                                        required=True,
                                        option_type=SlashCommandOptionType.CHANNEL

                                    )
                                ])
    async def incident_setup_ticket(self, ctx: SlashContext, mode, channel):

        if not ctx.author.guild_permissions.administrator:
            await ctx.send('You do not have permissions to execute this command')
            return

        server = TinyConnector.get_guild(ctx.guild.id)


        if mode == 'category':
            if not isinstance(channel, discord.CategoryChannel):
                await ctx.send('you need to specify a category, not a channel')
                return

            server.incident_section_id = channel.id
            await ctx.send('The incident category will be `{:s}`'.format(channel.name))

        elif mode == 'summary':
            if not isinstance(channel, discord.TextChannel):
                await ctx.send('you need to specify a text channel')
                return

            server.statement_ch_id = channel.id
            await ctx.send('The summary channel will be `{:s}`'.format(channel.name))

        elif mode == 'log':
            if not isinstance(channel, discord.TextChannel):
                await ctx.send('you need to specify a text channel')
                return

            server.log_ch_id = channel.id
            await ctx.send('The log channel will be `{:s}`'.format(channel.name))


        TinyConnector.update_guild(server)


def setup(client):
    client.add_cog(IncidentModule(client))

