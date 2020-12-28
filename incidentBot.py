
import discord
from discord.ext import commands

from util.verboseErrors import VerboseErrors
from lib.tinyConnector import TinyConnector



# define before Data class
def get_guild_based_prefix(bot, msg: discord.Message):
    # raise exception if not on DM
    # effectively ignoring all DMs
    if isinstance(msg.channel, discord.channel.TextChannel):
        return TinyConnector.get_guild_prefix(msg.guild.id)
    else:
        return '_'



token = open('token.txt', 'r').read()
client = commands.Bot(command_prefix=get_guild_based_prefix, description='Report an incident to the stewards')



PREFIX_HELP = '```prefix <string>\n\n'\
             '\t• set - the command prefix for this bot\n```'



@client.event
async def on_ready():
    # debug log
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    print('-----------')
    await client.change_presence(activity=discord.Game(name='*incident'))




@client.event
async def on_guild_remove(guild):
    TinyConnector._delete_guild(guild.id)


@client.command(name='prefix', help = 'change the prefix')
@commands.has_guild_permissions(administrator=True)
@commands.guild_only()
async def set_prefix(cmd, *prefix):
    if not prefix or prefix[0] == 'help':
        await cmd.send(PREFIX_HELP)
        return


    server = TinyConnector.get_guild(cmd.guild.id)
    server.prefix = prefix[0]
    TinyConnector.update_guild(server)

    await cmd.send('New prefix is `{:s}`'.format(server.prefix))



def main():
    client.load_extension(f'modules.EventModule')
    client.run(token)


if __name__ == '__main__':
    main()
