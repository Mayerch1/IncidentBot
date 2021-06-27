import os
import re
import io
import requests

from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord_slash import cog_ext, SlashContext, ComponentContext, SlashCommandOptionType
from discord_slash.utils import manage_components
from discord_slash.model import SlashCommandOptionType, ButtonStyle
from discord_slash.utils.manage_commands import create_option, create_choice

from lib.tinyConnector import TinyConnector
from lib.data import Incident, Driver, State

from consts import Consts


from util.verboseErrors import VerboseErrors
from util.interaction import ack_message, guess_target_section, guess_target_text, get_client_response, get_client_reaction, wait_confirm_deny

from util.displayEmbeds import incident_embed
from util.htm_gen import gen_html_report


fileserver_whitelist = [140150091607441408, 722746405453692989]

archive_directory = os.getenv("FS_ARCHIVE_DIRECTORY")
passcode_port = os.getenv("FS_PASSCODE_PORT")
archive_secret = os.getenv("FS_SECRET")
passcode_host = os.getenv("ARCHIVE_CONTAINER")

# print(f'arch_directory: {archive_directory}')
# print(f'archive_secret: {archive_secret}')
# print(f'passcode_port: {passcode_port}')
# print(f'passcode_host: {passcode_host}')

class IncidentModule(commands.Cog):

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


    def _component_factory(self, allow_revert=False, allow_skip=True, show_lock=False, show_edit=False):
        buttons = [
            manage_components.create_button(
                style=ButtonStyle.secondary,
                emoji='⏪',
                custom_id='incident_navigation_prev',
                disabled=(not allow_revert)
            ),
            manage_components.create_button(
                style=ButtonStyle.secondary,
                emoji='⏩',
                custom_id='incident_navigation_next',
                disabled=(not allow_skip)
            ),
        ]

        if show_lock:
            buttons.append(
                manage_components.create_button(
                    style=ButtonStyle.secondary,
                    emoji='🔒',
                    custom_id='incident_navigation_lock'
                )
            )

        if show_edit:
            buttons.append(
                manage_components.create_button(
                    style=ButtonStyle.secondary,
                    emoji='🔧',
                    custom_id='incident_navigation_edit'
                )
            )


        action_row = manage_components.create_actionrow(*buttons)
        return action_row


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

        comps = self._component_factory(allow_revert=True)

        embed = discord.Embed(title='Recommended Upload Solutions')
        embed.add_field(name='Streamable', value = '[choose for fast and simple upload](https://streamable.com)', inline=False)
        embed.add_field(name='Youtube', value = '[choose for more control over your upload](https://youtube.com)', inline=False)

        m1 = await channel.send('<@{:d}> Please upload the proof of the incident\n'.format(incident.victim.u_id),
                                embed=embed,
                                components=[comps])

        m2 = await channel.send('If you do not add any proof, this ticket might be closed without further notice.')

        incident.cleanup_queue.extend([m1.id, m2.id])

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

            comps = self._component_factory(allow_revert=True)
            msg = await channel.send('Use the navigation bar, once you\'re done', components=[comps])

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


        comps = self._component_factory(allow_revert=True)
        embed = discord.Embed(title='Recommended Upload Solutions')
        embed.add_field(name='Streamable', value = '[choose for fast and simple upload](https://streamable.com)', inline=False)
        embed.add_field(name='Youtube', value = '[choose for more control over your upload](https://youtube.com)', inline=False)

        q1 = await channel.send('<@{:d}> upload additional proof if you have some\n'.format(incident.offender.u_id),
                                    embed=embed,
                                    components=[comps])


        incident.cleanup_queue.append(q1.id)
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
                m2 = await channel.send('If all important footage is already posted, confirm this with an arbitrary message (e.g. `no further evidence`) and react again.')

                incident.cleanup_queue.extend([m1.id, m2.id])
                TinyConnector.update_incident(server.g_id, incident)
                return


        # only advance if proof check passes
        incident.state = State.STEWARD_STATEMENT


        await self._del_msg_list(channel, incident.cleanup_queue)
        incident.cleanup_queue = []

        await self._set_no_write(channel, victim, offender)


        comps = self._component_factory(allow_revert=True)

        q1 = await channel.send('<@&{:d}> please have a look at this incident and state your judgement.'.format(server.stewards_id),
                                components=[comps])

        incident.cleanup_queue.append(q1.id)
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

        if incident.infringement is None:
            incident.infringement = 'Not specified'

        q1 = await channel.send('<@&{:d}> please state the type of this incident (driver reported as `{:s}`)'.format(server.stewards_id, incident.infringement))
        category = await get_client_response(self.client, q1, 300)

        q2 = await channel.send('<@&{:d}> please state the outcome (and resulting penalty)'.format(server.stewards_id))
        outcome = await get_client_response(self.client, q2, 300)


        # re-fetch, as db could have changed
        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents[incident_id]

        # do not assign None, but use old/placeholder values
        incident.outcome = outcome if outcome else "N/A"
        incident.infringement = category if category else incident.infringement + ' (as reported by victim)'

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



        out_str = '<@&{:d}> you can close (🔒)  the incident at any point or modify the outcome (🔧) by reacting to it.'.format(server.stewards_id)
        comps = self._component_factory(allow_revert=True, allow_skip=False, show_lock=True, show_edit=True)

        eb = await channel.send(out_str,
                                embed=incident_embed(incident, channel.name[2:], incident.race_name),
                                components=[comps])

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

            comps = self._component_factory(allow_revert=True, allow_skip=False, show_lock=True, show_edit=True)
            eb = await channel.send(embed=incident_embed(incident, channel.name[2:], incident.race_name),
                                    components=[comps])

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


        # wolfpack specific file server
        if server.log_ch_id:
            log_ch = guild.get_channel(server.log_ch_id)

            if guild.id in fileserver_whitelist:
                html_str = await gen_html_report(channel, incident.victim.u_id, incident.offender.u_id, server.stewards_id, self.client.user.id)

                if html_str:
                    # generate folder structure and url
                    file_path = f'{channel.name[2:]}.html'
                    with open(f'{archive_directory}/{file_path}', 'w', encoding='utf-8') as fp:
                        fp.write(html_str)

                    await log_ch.send('get a link to the ticket log with `/incident logs`')

            # all other servers
            else:
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
                               ' Try cancelling the ticket (`/incident cancel`).')
            return


        if incident.state == State.VICTIM_PROOF:
            # there's no fixed method for this question
            # permissions do not change between states V_PROOF and V_STATEMENT

            req1 = await channel.send('Please take 1 or 2 paragraphs to state what happened, what effect it had on your race and '\
                                'why you think its a punishable behaviour (do not post links to footage yet)')

            incident.cleanup_queue.append(req1.id)
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
            m = await message.channel.send('In order to advance the ticket, you need to *click* the ⏩-Button on the navigation bar')
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



    @cog_ext.cog_component(components=[
                            'incident_navigation_next',
                            'incident_navigation_prev',
                            'incident_navigation_lock',
                            'incident_navigation_edit'
                            ])
    async def on_component_incident(self, ctx: ComponentContext):

        guild = ctx.guild
        channel = ctx.channel
        author_id = ctx.author_id

        server = TinyConnector.get_guild(guild.id)
        incident = server.active_incidents.get(channel.id, None)

        if not incident:
            return

        await ctx.defer(edit_origin=True)

        if ctx.component_id == 'incident_navigation_next':
            # advance over state-machine
            # increment the incident state before call, as async method could delay and lead to altered database
            if incident.state == State.VICTIM_STATEMENT and author_id == incident.victim.u_id:
                await self.incident_victim_proof(guild, channel.id, incident.channel_id)

            elif incident.state == State.VICTIM_PROOF and author_id == incident.victim.u_id:
                await self.incident_notify_offender(guild, channel.id, incident.channel_id, check_proof_exists=True)

            elif incident.state == State.OFFENDER_STATEMENT and author_id == incident.offender.u_id:
                await self.incident_offender_proof(guild, channel.id, incident.channel_id)

            elif incident.state == State.OFFENDER_PROOF and author_id == incident.offender.u_id:
                await self.incident_notify_stewards(guild, channel.id, incident.channel_id, check_proof_exists=True)

            elif incident.state == State.STEWARD_STATEMENT and self._is_member_steward(ctx.author, server.stewards_id):
                await self.incident_steward_sumup(guild, channel.id, incident.channel_id)
                await self.incident_steward_end_statement(guild, channel.id, incident.channel_id)

        elif ctx.component_id == 'incident_navigation_prev':
            if self._is_member_steward(ctx.author, server.stewards_id):
                await self.revert_incident_state(guild, channel.id, incident.channel_id)
            else:
                await channel.send('Only stewards can revert the ticket state')

        elif ctx.component_id == 'incident_navigation_lock':
            if incident.state == State.DISCUSSION_PHASE and self._is_member_steward(ctx.author, server.stewards_id):
                await self.incident_close_incident(guild, channel.id, incident.channel_id)

        elif ctx.component_id == 'incident_navigation_edit':
            if incident.state == State.DISCUSSION_PHASE and self._is_member_steward(ctx.author, server.stewards_id):
                await self.incident_modify_outcome(guild, channel.id, incident.channel_id, ctx.author)



    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):

        emoji = payload.emoji.name

        if emoji == '⏩':
            guild = self.client.get_guild(payload.guild_id)
            channel = guild.get_channel(payload.channel_id)

            await channel.send('The incident bot is not using reactions anymore. Please use the navigation bar below a previous message '\
                                'to advance the ticket')


    # =====================
    # commands functions
    # =====================



    @cog_ext.cog_subcommand(base='incident', name='cancel', description='cancel an active incident')
    async def incident_cancel(self, ctx: SlashContext):
        await self.cancel(ctx)


    @cog_ext.cog_subcommand(base='incident', name='logs', description='get access to the incident logs')
    @commands.guild_only()
    async def incident_archive(self, ctx: SlashContext):

        if ctx.guild.id not in fileserver_whitelist:
            await ctx.send('this server is not whitelisted for archive usage')
            return

        # the server will generate the authentication
        url = f'http://{passcode_host}:{passcode_port}/passcode'
        print(f'request to {url}')
        try:
            resp = requests.get(url, json={"secret": archive_secret})
        except:
            await ctx.send('Failed to create login credentials, please contact a moderator to resolve this issue')
            return

        if resp.status_code == 200:
            redirect = resp.content.decode('utf-8')
            redirect = redirect.replace(':4200', '') #TODO: workaround for unused port (reverse proxy doesn't need port)

            # try to send the link via dm
            # there's no fallback for a dm as the link shouldn't go public
            # a warning is issued on failure
            dm = await ctx.author.create_dm()
            try:
                await dm.send(redirect)
            except discord.errors.Forbidden as e:
                embed = discord.Embed(title='Missing DM Permission',
                                        description='Please [change your preferences]({:s}) and invoke this '\
                                                    'command again.\n You can revert the changes after you\'ve received the url to the archive.'
                                                    .format(r'https://support.discord.com/hc/en-us/articles/217916488-Blocking-Privacy-Settings-'),
                                        color=0xff0000)
                await ctx.send(embed = embed)
                return

            await ctx.send('I\'ve send you a temporary link to the archive')
        else:
            await ctx.send(f'Failed to create login credentials (code {resp.status_code}), please contact a moderator to resolve this issue')






def setup(client):
    client.add_cog(IncidentModule(client))

