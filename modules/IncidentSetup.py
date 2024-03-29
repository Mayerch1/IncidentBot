import os
import re
import io
import asyncio
import requests
from enum import Enum

from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord_slash import cog_ext, SlashContext, ComponentContext, SlashCommandOptionType
from discord_slash.context import MenuContext
from discord_slash.utils import manage_components
from discord_slash.model import SlashCommandOptionType, ButtonStyle, ContextMenuType
from discord_slash.utils.manage_commands import create_option, create_choice


from lib.tinyConnector import TinyConnector
from lib.data import Incident, Driver, State

from consts import Consts


from util.verboseErrors import VerboseErrors
from util.interaction import ack_message, get_client_response, get_client_reaction, wait_confirm_deny

from util.displayEmbeds import incident_embed


class IncidentSetup(commands.Cog):

    class SetupState(Enum):
        race_name = 0
        victim_name = 1
        victim_number = 2
        offender_name = 3
        offender_number = 4
        classification = 5
        classification_other = 6
        lap_corner = 7
        summary = 8

        # negative numbers will never be hit by the cyclic advance
        exit_abort = -1
        exit_success =  -2
        exit_timeout = -3
        exit_unexpected = -4

        def max_enum_val(self):
            return max(map(lambda e: e.value, list(IncidentSetup.SetupState)))

        def next(self):
            if self.value+1 > self.max_enum_val():
                return IncidentSetup.SetupState(0)
            else:
                return IncidentSetup.SetupState(self.value+1)

        def prev(self):
            if self.value-1 < 0:
                return IncidentSetup.SetupState(self.max_enum_val())
            else:
                return IncidentSetup.SetupState(self.value-1)


    class STM():
        def __init__(self):
            self.state = IncidentSetup.SetupState.race_name
            self.guild = None
            self.author = None
            self.incident = None
            self.navigation_rows = []
            self.question_msg = None
            self.dm = None
            self.setup_satisfied = False


    # =====================
    # internal functions
    # =====================
    def __init__(self, client):
        self.client = client



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
    ## Incident Creation - this is basically an stm
    #################################################

    async def _setup_stm(self, cmd, stm):

        perms = discord.Permissions()
        perms.manage_channels = True
        perms.send_messages = True
        perms.read_message_history = True
        perms.add_reactions = True
        perms.manage_permissions = True
        perms.embed_links = True

        server = TinyConnector.get_settings(cmd.guild.id)

        if not server.incident_section_id:
            await cmd.send('You need to setup a section for incidents first. Please ask an admin to use `/incident setup`')
            return

        if not server.statement_ch_id:
            await cmd.send('You need to setup a summary channel for incidents first. Please ask an admin to use `/incident setup`')
            return

        if not server.stewards_id:
            await cmd.send('No steward role is specified. Please ask an admin to use `/incident setup')
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

        # do not send for MenuContext
        if isinstance(cmd, SlashContext):
            await cmd.channel.send('This incident is now looked at by the stewards. There\'s no need for further discussions in this channel.')

        stm.dm = dm
        return


    async def _abort_stm(self, stm):
        await stm.dm.send('Cancelling ticket. You can start again by reinvoking the command.')

        for n_row in stm.navigation_rows:
            for c in n_row['components']:
                c['disabled'] = True

        await stm.question_msg.edit(components=[*stm.navigation_rows])


    async def _abort_unexpected_stm(self, stm):
        await stm.dm.send('Unexpected error ocurred. Please try again and contact a bot-moderator.')

        for n_row in stm.navigation_rows:
            for c in n_row['components']:
                c['disabled'] = True

        await stm.question_msg.edit(components=[*stm.navigation_rows])


    async def _timeout_stm(self, stm):
        await stm.dm.send('The ticket timeout was exceeded. You can start the process again by reinvoking the command.')

        for n_row in stm.navigation_rows:
            for c in n_row['components']:
                c['disabled'] = True

        await stm.question_msg.edit(components=[*stm.navigation_rows])

    async def _success_stm(self, stm):

        await stm.dm.send('You completed the ticket initialization. Please head back to the server to enter further incident details.')

        success = await self.incident_setup_channel(stm)

        if success:
            TinyConnector.incr_inc_cnt(stm.guild.id)
            TinyConnector.update_incident(stm.incident)

            await stm.dm.send('I tagged you in the appropriate incident channel.')

        for n_row in stm.navigation_rows:
            for c in n_row['components']:
                c['disabled'] = True

        await stm.question_msg.edit(components=[*stm.navigation_rows])


    def get_question(self, stm):

        state = stm.state
        l = state.max_enum_val()+1

        if state == IncidentSetup.SetupState.race_name:
            return discord.Embed(title=f'({state.value+1}/{l}) State the Game- and Race-name', description='Type your answer into the chat')
        elif state == IncidentSetup.SetupState.victim_name:
            return discord.Embed(title=f'({state.value+1}/{l}) State your drivers in-game name', description='Type your answer into the chat')
        elif state == IncidentSetup.SetupState.victim_number:
            return discord.Embed(title=f'({state.value+1}/{l}) State your car number', description='Only digits are allowed')
        elif state == IncidentSetup.SetupState.offender_name:
            return discord.Embed(title=f'({state.value+1}/{l}) State the other drivers name', description='Type your answer into the chat')
        elif state == IncidentSetup.SetupState.offender_number:
            return discord.Embed(title=f'({state.value+1}/{l}) State the other drivers number', description='Only digits are allowed')
        elif state == IncidentSetup.SetupState.classification:
            return discord.Embed(title=f'({state.value+1}/{l}) Select the incident classification', description='select `other` if no element in the list describes your incident')
        elif state == IncidentSetup.SetupState.classification_other:
            return discord.Embed(title=f'({state.value+1}/{l}) State the incident classification', description='Type your custom classification below')
        elif state == IncidentSetup.SetupState.lap_corner:
            return discord.Embed(title=f'({state.value+1}/{l}) If possible, state the race lap and corner', description='\'-\' if unspecified')
        elif state == IncidentSetup.SetupState.summary:
            return incident_embed(stm.incident, 'Incident details', stm.incident.race_name)
        else:
            return discord.Embed(title=f'Internal Error, please use ⏪ or ⏩ and notify a bot-moderator')


    async def update_navigation(self, stm: STM, push_update=False):

        buttons = [
            manage_components.create_button(
                style=ButtonStyle.secondary,
                emoji='⏪',
                custom_id='setup_navigation_prev',
                disabled=((not stm.setup_satisfied) and (stm.state == IncidentSetup.SetupState.race_name)) # disable on first message
            ),
            manage_components.create_button(
                style=ButtonStyle.secondary,
                emoji='⏩',
                custom_id='setup_navigation_next',
                disabled=(not stm.setup_satisfied)  # only allow navigation once setup is finished
            ),
            manage_components.create_button(
                style=ButtonStyle.danger,
                label='Abort',
                custom_id='setup_navigation_abort'
            )
        ]

        if stm.setup_satisfied:
            buttons.append(
                manage_components.create_button(
                    style=ButtonStyle.success,
                    label='Done',
                    custom_id='setup_navigation_complete'
                )
            )

        stm.navigation_rows = [manage_components.create_actionrow(*buttons)]

        if stm.state == IncidentSetup.SetupState.classification:
            inc_selection = (
                manage_components.create_select(
                    custom_id='setup_navigation_incident_type',
                    placeholder='Select the Incident type',
                    min_values=1,
                    max_values=1,
                    options=[
                        manage_components.create_select_option(
                            label='Impeding a qualifying lap',
                            value='impeding_qualy'
                        ),
                        manage_components.create_select_option(
                            label='Severe cut',
                            value='severe_cut',
                            description='Severe cut with a lasting advantage'
                        ),
                        manage_components.create_select_option(
                            label='Illegal overtake',
                            value='illegal_overtake'
                        ),
                        manage_components.create_select_option(
                            label='Making Contact',
                            value='contact_straight',
                            description='Making Contact on a straight'
                        ),
                        manage_components.create_select_option(
                            label='Rear ending',
                            value='rear_ending',
                            description='Rear ending in the braking zone'
                        ),
                        manage_components.create_select_option(
                            label='Move under braking',
                            value='move_braking',
                            description='Moving in the braking zone'
                        ),
                        manage_components.create_select_option(
                            label='Multiple Defensive moves',
                            value='multiple_defence'
                        ),
                        manage_components.create_select_option(
                            label='Failing to respect space',
                            value='cutting_off',
                            description='Failing to respect the available space'
                        ),
                        manage_components.create_select_option(
                            label='Slow-Down on racing line',
                            value='slow_down',
                            description='Taking a slow-down on the Racing Line'
                        ),
                        manage_components.create_select_option(
                            label='Blocking under blue flags',
                            value='blue_flags'
                        ),
                        manage_components.create_select_option(
                            label='Forcing off the track',
                            value='forcing_offtrack'
                        ),
                        manage_components.create_select_option(
                            label='Unsafe Rejoin',
                            value='unsafe_rejoin',
                            description='Rejoining the track in an unsafe manner'
                        ),
                        manage_components.create_select_option(
                            label='Failing to wait',
                            value='fail_to_wait',
                            description='Failing to wait after causing an incident'
                        ),
                        manage_components.create_select_option(
                            label='Reckless Driving',
                            value='reckless_driving'
                        ),
                        manage_components.create_select_option(
                            label='Other',
                            value='other',
                            description='Enter a custom text, if none of the options apply'
                        )
                    ]
                )
            )
            stm.navigation_rows.append(manage_components.create_actionrow(inc_selection))

        if stm.question_msg and push_update:
            await stm.question_msg.edit(components=[*stm.navigation_rows])


    async def update_messages(self, stm, re_send=False):

        if re_send:
            await stm.question_msg.delete()
            stm.question_msg = await stm.dm.send(content='...')

        question_eb = self.get_question(stm)
        await stm.question_msg.edit(content='', embed=question_eb, components=[*stm.navigation_rows])


    def classification_selection(self, ctx, stm):
        sel_id = ctx.selected_options[0]  # only allow a single selection

        # filter the component given the selected id
        # component must exist, otherwise event couldn't be triggered in the first place
        opts = ctx.component['options']
        sel_opt = list(filter(lambda o: o['value'] == sel_id, opts))[0]

        stm.incident.infringement = sel_opt.get('description', None) or sel_opt['label']

        # overskip the other-selection state
        # if the incident is not other
        stm.state = stm.state.next()
        if sel_id != 'other':
            stm.state = stm.state.next()


    async def process_navigation(self, ctx, stm):
        if ctx.component_id == 'setup_navigation_next':
            stm.state = stm.state.next()
        elif ctx.component_id == 'setup_navigation_prev':
            stm.state = stm.state.prev()
        elif ctx.component_id == 'setup_navigation_complete':
            stm.state = IncidentSetup.SetupState.exit_success
        elif ctx.component_id == 'setup_navigation_abort':
            stm.state = IncidentSetup.SetupState.exit_abort
        elif ctx.component_id == 'setup_navigation_incident_type':
            self.classification_selection(ctx, stm)

        await ctx.defer(edit_origin=True)


    async def process_msg(self, msg, stm: STM):

        content = msg.content

        if stm.state == IncidentSetup.SetupState.race_name:
           stm.incident.race_name = content

        elif stm.state == IncidentSetup.SetupState.victim_name:
            stm.incident.victim.name = content

        elif stm.state == IncidentSetup.SetupState.victim_number:
            if not content.isdigit():
                await stm.dm.send('Please enter a number')
                return False
            else:
                stm.incident.victim.number = int(content)

        elif stm.state == IncidentSetup.SetupState.offender_name:
            stm.incident.offender.name = content

        elif stm.state == IncidentSetup.SetupState.offender_number:
            if not content.isdigit():
                await stm.dm.send('Please enter a number')
                return False
            else:
                stm.incident.offender.number = int(content)

        elif stm.state == IncidentSetup.SetupState.classification_other:
            stm.incident.infringement = content

        elif stm.state == IncidentSetup.SetupState.lap_corner:
            stm.incident.lap = content
            stm.setup_satisfied = True

        # these states only allow advancement by components
        # not by entering a text message
        if stm.state != IncidentSetup.SetupState.classification:
            stm.state = stm.state.next()

        return True


    async def incident_stm(self, ctx, offender_id: int):

        await ctx.defer()

        stm = IncidentSetup.STM()
        stm.guild = ctx.guild
        stm.author = ctx.author
        await self._setup_stm(ctx, stm)

        if stm.dm is None:
            return

        incident = Incident({'g_id': ctx.guild.id})

        incident.victim.u_id = ctx.author.id
        incident.offender.u_id = offender_id

        stm.incident = incident

        await self.update_navigation(stm)
        stm.question_msg = await stm.dm.send(content='...', components=[*stm.navigation_rows])

        re_send_msg = False

        while True:

            await self.update_navigation(stm, push_update=False)  # let update_message do the i/o
            await self.update_messages(stm, re_send=re_send_msg)
            re_send_msg = False

            def msg_check(msg):
                return msg.author.id == stm.dm.recipient.id and msg.channel.id == stm.dm.id

            pending_tasks = [manage_components.wait_for_component(self.client, components=[*stm.navigation_rows], timeout=10*60),
                            self.client.wait_for('message',check=msg_check)]

            done_tasks, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            # cancel the failed tasks
            for task in pending_tasks:
                task.cancel()

            # only process the first 'done' task
            # ignore any potential secondary tasks
            # if no tasks, abort the cycle (timeout)
            if not done_tasks:
                return

            first_task = done_tasks.pop()
            ex = first_task.exception()

            # ignore all other tasks
            while done_tasks:
                task = done_tasks.pop()
                task.cancel()
                _ = task.exception()  # reading this silences console/log warnings

            if ex:
                if isinstance(ex, asyncio.exceptions.TimeoutError):
                    stm.state = IncidentSetup.SetupState.exit_timeout
                else:
                    stm.state = IncidentSetup.SetupState.exit_unexpected
            else:

                result = await first_task

                if isinstance(result, ComponentContext):
                    await self.process_navigation(result, stm)
                elif isinstance(result, discord.Message):
                    re_send_msg = await self.process_msg(result, stm)


            if stm.state == IncidentSetup.SetupState.exit_abort:
                await self._abort_stm(stm)
                return
            elif stm.state == IncidentSetup.SetupState.exit_timeout:
                await self._timeout_stm(stm)
                return
            elif stm.state == IncidentSetup.SetupState.exit_unexpected:
                await self._abort_unexpected_stm(stm)
                return
            elif stm.state == IncidentSetup.SetupState.exit_success:
                await self._success_stm(stm)
                return


    async def incident_setup_channel(self, stm):

        # need update for incident number, otherwise concurrent access might break sequential numbering
        server = TinyConnector.get_settings(stm.guild.id)
        incident_cnt = TinyConnector.get_inc_cnt(stm.guild.id)

        # create channel and ask user for more input
        ch_name = '🅰 Incident Ticket - {:d}'.format(incident_cnt + 1)

        section = self.client.get_channel(server.incident_section_id)
        steward_role = stm.guild.get_role(server.stewards_id)

        offender = await stm.guild.fetch_member(stm.incident.offender.u_id)

        if section is None:
            await stm.dm.send('Failed to create a channel, please ask an admin to re-set the category with `/incident setup`')
            return False


        inc_channel = await stm.guild.create_text_channel(ch_name, category=section)
        stm.incident.channel_id = inc_channel.id


        # ===================================
        # THO ORDER OF THIS IS VERY IMPORTANT
        # @EVERYONE NEEDS TO BE SET LAST
        # ===================================

        try:
            await inc_channel.set_permissions(stm.guild.me, manage_messages=True, read_messages=True, send_messages=True, read_message_history=True)
        except Exception as e:
            print('bot permissions:')
            print(e)

        try:
            await inc_channel.set_permissions(steward_role, read_messages=True, send_messages=True, read_message_history=True)
        except Exception as e:
            print('steward permission:')
            print(e)

        await self._set_victim_write(inc_channel, stm.author, offender)

        try:
            await inc_channel.set_permissions(stm.guild.default_role, read_messages=False, send_messages=False, read_message_history=False)
        except Exception as e:
            print('everyone permissions:')
            print(e)


        buttons = [
            manage_components.create_button(
                style=ButtonStyle.secondary,
                emoji='⏪',
                custom_id='incident_navigation_prev',
                disabled=True
            ),
            manage_components.create_button(
                style=ButtonStyle.secondary,
                emoji='⏩',
                custom_id='incident_navigation_next'
            ),
        ]
        action_row = manage_components.create_actionrow(*buttons)

        # ask the initial question, from then on, handling is done in events

        embed_msg = await inc_channel.send(embed=incident_embed(stm.incident, ch_name[2:], stm.incident.race_name))


        req1 = await inc_channel.send('{:s} Please take 1 or 2 paragraphs to state what happened, what effect it had on your race and '\
                                'why you think its a punishable behaviour (do not post links to footage yet)'.format(stm.author.mention))


        req2 = await inc_channel.send('Use the navigation bar to proceed to the next step, once you stated all points', components=[action_row])

        stm.incident.cleanup_queue.append(req1.id)
        stm.incident.cleanup_queue.append(req2.id)

        return True


    # =====================
    # events functions
    # =====================

    @commands.Cog.listener()
    async def on_ready(self):
        print('IncidentSetup loaded')


    # =====================
    # context menu functions
    # =====================


    @cog_ext.cog_context_menu(name='Report_Incident', target=ContextMenuType.USER)
    async def incident_report_context(self, ctx: MenuContext):

        target_id = ctx.target_author.id
        await self.incident_stm(ctx, int(target_id))

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
        await self.incident_stm(ctx, offender.id)


def setup(client):
    client.add_cog(IncidentSetup(client))

