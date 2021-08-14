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
from discord_slash.utils import manage_components
from discord_slash.model import SlashCommandOptionType, SlashCommandPermissionType,ButtonStyle
from discord_slash.utils.manage_commands import create_option, create_choice, create_permission


from lib.tinyConnector import TinyConnector
from lib.data import Incident, Driver, State

from consts import Consts


from util.verboseErrors import VerboseErrors
from util.interaction import ack_message, get_client_response, get_client_reaction, wait_confirm_deny

from util.displayEmbeds import incident_embed



class IncidentSettings(commands.Cog):


    # =====================
    # internal functions
    # =====================
    def __init__(self, client):
        self.client = client



    # =====================
    # events functions
    # =====================

    @commands.Cog.listener()
    async def on_ready(self):
        print('IncidentSettings loaded')


    # =====================
    # commands functions
    # =====================

    @cog_ext.cog_subcommand(base='incident_setup', name='roles', description='setup the incident roles (admin)',
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
    @cog_ext.permission(guild_id=722746405453692989, # Wolfpack
                        permissions=[
                            create_permission(
                                id=722746405453692989, # everyone
                                id_type=SlashCommandPermissionType.ROLE,
                                permission=False
                            ),
                            create_permission(
                                id=727959973388091522, # Server Member
                                id_type=SlashCommandPermissionType.ROLE,
                                permission=False
                            ),
                            create_permission(
                                id=790295518869717022, # Bot Dev
                                id_type=SlashCommandPermissionType.ROLE,
                                permission=True
                            ),
                            create_permission(
                                id=727960161087520821, # Moderator
                                id_type=SlashCommandPermissionType.ROLE,
                                permission=True
                            ),
                            create_permission(
                                id=727961402035273830, # Owner
                                id_type=SlashCommandPermissionType.ROLE,
                                permission=True
                            )

                        ])
    async def incident_setup_steward(self, ctx: SlashContext, mode, role):

        if mode == 'steward':
            server = TinyConnector.get_settings(ctx.guild.id)
            server.stewards_id = int(role.id)
            TinyConnector.update_settings(server)

            await ctx.send(f'New steward role is {role.mention}')


    @cog_ext.cog_subcommand(base='incident_setup', name='channels', description='setup tho incident channels (admin)',
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

        server = TinyConnector.get_settings(ctx.guild.id)

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

        TinyConnector.update_settings(server)


def setup(client):
    client.add_cog(IncidentSettings(client))

