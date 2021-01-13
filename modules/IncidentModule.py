import os
import re

from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks


from lib.tinyConnector import TinyConnector
from lib.data import Incident, Driver, State


from util.verboseErrors import VerboseErrors
from util.interaction import ack_message, guess_target_section, guess_target_text, get_client_response, wait_confirm_deny

from util.displayEmbeds import incident_embed


class IncidentModule(commands.Cog):

    INC_HELP   = '```*incident <command>\n'\
                 '\t[@mention] - open a new ticket [default]\n'\
                 '\tcancel     - abort an opened ticket (do not use for ticket closing)\n'\
                 '\tsetup      - set some properties (use \'incident setup help\')```'

    SETUP_HELP = '```*incident setup <command>\n'\
                 '\tcategory - set the category where channels are created\n'\
                 '\tstewards - set the steward role\n'\
                 '\tsummary  - set the summary channel\n'\
                 '\thelp     - show this message```'


    # =====================
    # internal functions
    # =====================
    def __init__(self, client):
        self.client = client

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


    #################################################
    ## Incident Setup - Set Config values, for admins
    #################################################


    async def setup_category(self, cmd):

        q = await cmd.send('Please enter the name of the channel-category used for incidents.')
        resp = await get_client_response(self.client, q, 120, cmd.author)

        if resp is None:
            return

        section = await guess_target_section(cmd, resp, cmd.guild.channels, cmd.channel, True)

        if section is None:
            await cmd.send('Failed to set the category channel')
            return

        await cmd.send('The incident category will be `{:s}`'.format(section.name))

        server = TinyConnector.get_guild(cmd.guild.id)
        server.incident_section_id = section.id
        TinyConnector.update_guild(server)



    async def setup_summary_ch(self, cmd):

        q = await cmd.send('Please enter the name of the summary channel, used for publishing ticket summaries')
        resp = await get_client_response(self.client, q, 120, cmd.author)

        channel = await guess_target_text(cmd, resp, cmd.guild.channels, cmd.channel, True)

        if channel is None:
            await cmd.send('Failed to set the summary channel')
            return

        await cmd.send('The summary channel will be `{:s}`'.format(channel.name))


        server = TinyConnector.get_guild(cmd.guild.id)
        server.statement_ch_id = channel.id
        TinyConnector.update_guild(server)



    async def setup_stewards(self, cmd):

        def is_role_mention(input):
            match = re.findall("@&\d+", input)
            return (len(match) > 0)


        q = await cmd.send('Please tag the steward role used for incident handling.')
        resp = await get_client_response(self.client, q, 120, cmd.author, is_role_mention)

        steward_id = re.findall('@&\d+', resp)[0][2:]


        server = TinyConnector.get_guild(cmd.guild.id)
        server.stewards_id = int(steward_id)
        TinyConnector.update_guild(server)






    async def setup(self, cmd, mode):

        if not cmd.author.guild_permissions.administrator:
            await cmd.send('You do not have permissions to execute this command')
            return

        if mode:
            if mode[0] == 'category':
                await self.setup_category(cmd)

            elif mode[0] == 'stewards':
                await self.setup_stewards(cmd)

            elif mode[0] == 'summary':
                await self.setup_summary_ch(cmd)

            else:
                await cmd.send(IncidentModule.SETUP_HELP)

        else:
            await self.setup_category(cmd)
            await self.setup_stewards(cmd)
            await self.setup_summary_ch(cmd)


        await ack_message(cmd.message)



    #################################################
    ## Cancel an active ticket - if permissions avail
    #################################################

    async def cancel(self, cmd, mode):

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


        TinyConnector.update_guild(server)



    #################################################
    ## Incident Creation - Initial question on victim
    #################################################
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

        if not server.incident_section_id:
            await cmd.send('You need to setup a section for incidents first. Please ask an admin to use `{:s}incident setup`'.format(server.prefix))
            return

        if not server.stewards_id:
            await cmd.send('No steward role is specified. Please ask an admin to use `{:s}incident setup'.format(server.prefix))
            return


        section = self.client.get_channel(server.incident_section_id)

        if not await VerboseErrors.show_missing_perms("incident", perms, section, channel_overwrite=True, text_alternative=cmd.channel):
            return



        dm = await cmd.author.create_dm()
        await cmd.send('I have sent you a DM to continue the ticket process.')

        if dm is None:
            return

        # confirm the message and carry on in dms
        await ack_message(cmd.message)


        incident = await self.incident_setup_dm(cmd, dm)
        if incident is None:
            return


        incident.offender.u_id = offender_id
        incident = await self.incident_setup_channel(cmd, server, incident)


        # re-fetch guild, as it could have changed
        server = TinyConnector.get_guild(cmd.guild.id)
        server.active_incidents[incident.channel_id] = incident
        server.incident_cnt += 1
        TinyConnector.update_guild(server)




    async def incident_setup_dm(self, cmd, dm):
        embed = discord.Embed(title='New Incident Ticket',
                              description='Please answer the following questions or ignore me to cancel the ticket.')
        await dm.send(embed=embed)

        incident = Incident()
        incident.victim = Driver()
        incident.offender = Driver()

        incident.victim.u_id = cmd.author.id

        return incident


        q = await dm.send('State the Game- and Race-name:')
        r = await get_client_response(self.client, q, 60, cmd.author)

        if r is None:
            return

        incident.race_name = r

        q = await dm.send('State your drivers in-game name:')
        r = await get_client_response(self.client, q, 60, cmd.author)

        if r is None:
            return None
        incident.victim.name = r

        q = await dm.send('State your car number:')
        r = await get_client_response(self.client, q, 60, cmd.author, lambda x: x.isdigit())

        if r is None:
            return None
        incident.victim.number = int(r)

        q = await dm.send('State the other drivers name:')
        r = await get_client_response(self.client, q, 60, cmd.author)

        if r is None:
            return None
        incident.offender.name = r

        q = await dm.send('State the other drivers number:')
        r = await get_client_response(self.client, q, 60, cmd.author, lambda x: x.isdigit())

        if r is None:
            return None
        incident.offender.number = int(r)

        q = await dm.send('If possible, state the race lap and corner, (- if unspecified).')
        r = await get_client_response(self.client, q, 60, cmd.author)

        if r is None:
            return None
        incident.lap = r

        embed_msg = await dm.send(embed=incident_embed(incident, "Event details", incident.race_name))

        if not await wait_confirm_deny(self.client, embed_msg, 60, cmd.author):
            await dm.send('Cancelling ticket')
            return None

        return incident


    async def incident_setup_channel(self, cmd, server, incident):

        # create channel and ask user for more input
        ch_name = 'Incident Ticket - {:d}'.format(server.incident_cnt + 1)

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
        # EVERYONE NEEDS TO BE SET LAST
        # ===================================

        await inc_channel.set_permissions(cmd.guild.me, manage_messages=True, read_messages=True, send_messages=True)

        #try:
        await inc_channel.set_permissions(steward_role, read_messages=True, send_messages=True)

        #try:
        await inc_channel.set_permissions(offender, read_messages=True, send_messages=False)

        #try:
        await inc_channel.set_permissions(cmd.message.author, read_messages=True, send_messages=True)


        await inc_channel.set_permissions(cmd.guild.default_role, read_messages=False, send_messages=False)




        # ask the initial question, from then on, handling is done in events

        embed_msg = await inc_channel.send(embed=incident_embed(incident, ch_name, "Incident details"))
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

        m2 = await channel.send('If possible you should provide the 1st person- and the chase- camera for both cars.\n')

        embed = discord.Embed(title='Recommended Upload Solutions')
        embed.add_field(name='Streamable', value = '[choose for fast and simple upload](https://streamable.com)', inline=False)
        embed.add_field(name='Youtube', value = '[choose for more control over your upload](https://youtube.com)', inline=False)

        m3 = await channel.send(embed=embed)

        # skip forwad emoji
        msg = await channel.send('React with ⏩ once you added all proof')
        await msg.add_reaction('⏩')


        incident.cleanup_queue.extend([m1.id, m2.id, m3.id, msg.id])

        TinyConnector.update_guild(server)



    async def incident_notify_offender(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        victim = await guild.fetch_member(incident.victim.u_id)
        offender = await guild.fetch_member(incident.offender.u_id)

        # do not change state-machine yet
        # next step requires a valid offender-id to be entered
        # if this fails, the state machine remains in the previous state


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        # delete the old questions, this helps in keeping the channel clean
        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []

        # fake a text message
        # this will reset the state-machine watchdog
        # otherwise it would trigger this message at each iteration, after it was aborted once
        t = datetime.now()
        incident.last_msg = t.timestamp()


        # save is required here, as next steps will require delay
        TinyConnector.update_guild(server)


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
            await msg.add_reaction('⏩')

            # incr. state-machine on successfull offender-determination
            incident.state = State.OFFENDER_STATEMENT


            await channel.set_permissions(victim, read_messages=True, send_messages=False)
            await channel.set_permissions(offender, read_messages=True, send_messages=True)

            incident.cleanup_queue.extend([msg.id, q2.id])


        TinyConnector.update_guild(server)




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
        TinyConnector.update_guild(server)




    async def incident_notify_stewards(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        incident.state = State.STEWARD_STATEMENT

        victim = await guild.fetch_member(incident.victim.u_id)
        offender = await guild.fetch_member(incident.offender.u_id)

        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)

        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        await channel.set_permissions(victim, read_messages=True, send_messages=False)
        await channel.set_permissions(offender, read_messages=True, send_messages=False)



        embed = discord.Embed(title='Poll', description='Vote if you think this incident should be punished or not')
        embed.set_footer(text='This is only to get a general mood of the stewards. The decision is in no way bound by this poll')
        embed_msg = await channel.send(embed=embed)
        await embed_msg.add_reaction('✅')
        await embed_msg.add_reaction('❌')


        q1 = await channel.send('<@&{:d}> please have a look at this incident and state your judgement.'.format(server.stewards_id))
        msg = await channel.send('React with ⏩ once the final steward statement is issued. You can allow both parties to respond to your statement')

        await msg.add_reaction('⏩')


        incident.cleanup_queue.extend([q1.id, msg.id])
        TinyConnector.update_guild(server)




    async def incident_steward_sumup(self, guild, channel_id, incident_id):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # incident.state += 1


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []


        TinyConnector.update_guild(server)


        q1 = await channel.send('<@&{:d}> please state the category of infringement which was judged in 1 short sentence (e.g. \'causing a collision\', \'abuse of track limits\', ...)'.format(server.stewards_id))
        category = await get_client_response(self.client, q1, 60)

        q2 = await channel.send('<@&{:d}> please state the action taken in 1 short sentence (e.g. \'1st warning\', \'racing incident\', ...)'.format(server.stewards_id))
        outcome = await get_client_response(self.client, q2, 60)


        # re-fetch, as db could have changed
        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # even assign on None
        incident.outcome = outcome
        incident.infringement = category

        incident.cleanup_queue.extend([q1.id, q2.id])

        TinyConnector.update_guild(server)




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


        TinyConnector.update_guild(server)


        msg = await channel.send('<@&{:d}> you can close the incident at any point by reacting with 🔒'.format(server.stewards_id))
        await msg.add_reaction('🔒')


        await channel.send(embed=incident_embed(incident, channel.name, incident.race_name))
        await channel.send('<@!{:d}>, <@!{:d}> please review the stewards statement. '\
                            'You can respond to the judgement until the incident is locked by a steward.'
                            .format(incident.victim.u_id, incident.offender.u_id))


        await channel.set_permissions(victim, read_messages=True, send_messages=True)
        await channel.set_permissions(offender, read_messages=True, send_messages=True)



    async def incident_close_incident(self, guild, channel_id, incident_id, closing_steward = None):

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        incident.state = State.CLOSED_PHASE
        incident.locked_time = datetime.now().timestamp()

        TinyConnector.update_guild(server)


        # incident id is channel id
        channel = guild.get_channel(incident.channel_id)


        if closing_steward:

            await channel.send('{:s} please watch your DMs to close this ticket'.format(closing_steward.mention))

            dm = await closing_steward.create_dm()
            await dm.send(embed=incident_embed(incident, channel.name, incident.race_name))


            await dm.send('If the outcome has changed since the steward statement was issued, please enter the outcome:')
            await dm.send('Ignore this messages and the ticket will be closed in 60 seconds as shown above.\n')


            q1 = await dm.send('Please correct the action taken in 1 short sentence.')
            outcome = await get_client_response(self.client, q1, 60, closing_steward)

            if outcome:
                # re-fetch, as db could have changed
                # only assign if outcome has changed
                server = TinyConnector.get_guild(guild.id)
                incident = server.active_incidents[incident_id]
                incident.outcome = outcome
                TinyConnector.update_guild(server)


        if server.statement_ch_id:
            statement_ch = guild.get_channel(server.statement_ch_id)
            await statement_ch.send(embed = incident_embed(incident, channel.name, incident.race_name))


        await channel.send('The ticket is closed, please do not interact with this channel anymore.')





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

        del server.active_incidents[incident_id]
        TinyConnector.update_guild(server)

        # silent fail?
        await channel.delete()





    # =====================
    # events functions
    # =====================

    @commands.Cog.listener()
    async def on_ready(self):
        print('IncidentModule loaded')
        self.incident_timeout.start()



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

        # timeout counter for closing the incident
        t = datetime.now()
        incident.last_msg = t.timestamp()

        TinyConnector.update_guild(server)



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
                    # channel was deleted and incident is dangling
                    # TODO: decide later what to do
                    return


                if incident.state == State.VICTIM_STATEMENT and delta > timedelta(minutes=30):
                    await self.incident_victim_proof(guild, channel.id, incident.channel_id)

                elif incident.state == State.VICTIM_PROOF and delta > timedelta(minutes=30):
                    # if this fails, the state-machine will not advance
                    # this will lead to continuous pinging of the victim (by design)
                    await self.incident_notify_offender(guild, channel.id, incident.channel_id)

                # offender got 1 day for initial statement
                elif incident.state == State.OFFENDER_STATEMENT and delta > timedelta(days=1):
                    await self.incident_offender_proof(guild, channel.id, incident.channel_id)

                # further 2 hours for upload of proof
                elif incident.state == State.OFFENDER_PROOF and delta > timedelta(hours=2):
                    await self.incident_notify_stewards(guild, channel.id, incident.channel_id)

                # the stewards got 2 days of reaction
                elif incident.state == State.STEWARD_STATEMENT and delta > timedelta(days=2):
                    await self.incident_steward_sumup(guild, channel.id, incident.channel_id)
                    await self.incident_steward_end_statement(guild, channel.id, incident.channel_id)


                # currently state 4->6, as state 5 does not need user interaction

                # the incident is auto-closed after 1 further day
                elif incident.state == State.DISCUSSION_PHASE and delta > timedelta(days=1):
                    await self.incident_close_incident(guild, channel.id, incident.channel_id, None)


                # state 7 is closed incident with no further interaction
                # it will be deleted after a certain timedelta
                elif incident.state == State.CLOSED_PHASE:
                    # channel is deleted after 2 more days (for record)
                    delta = t - datetime.fromtimestamp(incident.locked_time)
                    if delta > timedelta(days=2):
                        await self.incident_delete(guild, inc_key)



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
                await self.incident_notify_offender(guild, channel.id, incident.channel_id)

            elif incident.state == State.OFFENDER_STATEMENT and payload.member.id == incident.offender.u_id:
                await self.incident_offender_proof(guild, channel.id, incident.channel_id)

            elif incident.state == State.OFFENDER_PROOF and payload.member.id == incident.offender.u_id:
                await self.incident_notify_stewards(guild, channel.id, incident.channel_id)

            elif incident.state == State.STEWARD_STATEMENT and self._is_member_steward(payload.member, server.stewards_id):
                await self.incident_steward_sumup(guild, channel.id, incident.channel_id)
                await self.incident_steward_end_statement(guild, channel.id, incident.channel_id)


            # state 7 is closed incident with no further interaction
            # it will be deleted after a certain timedelta

        elif payload.emoji.name == '🔒':
            if incident.state == State.DISCUSSION_PHASE and self._is_member_steward(payload.member, server.stewards_id):
                await self.incident_close_incident(guild, channel.id, incident.channel_id, payload.member)


            # state 7 is closed incident with no further interaction
            # it will be deleted after a certain timedelta



    # =====================
    # commands functions
    # =====================

    @commands.command(name='incident', help='Open Event-registration')
    @commands.guild_only()
    @commands.has_guild_permissions()
    async def incident_cmd(self, cmd, *mode):

        if mode and mode[0] == 'cancel':
            await self.cancel(cmd, mode[1:])

        elif mode and mode[0] == 'setup':
            await self.setup(cmd, mode[1:])

        elif len(cmd.message.mentions) > 0:
            await self.incident(cmd, cmd.message.mentions[0].id)

        else:
            await cmd.send(IncidentModule.INC_HELP)


def setup(client):
    client.add_cog(IncidentModule(client))
