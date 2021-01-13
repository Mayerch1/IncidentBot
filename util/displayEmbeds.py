import discord
from lib.data import Incident, Driver



def incident_embed(inc: Incident, title, description):
    embed = discord.Embed(title=title, description=description)


    embed.add_field(name='Victim', value='{:s} - {:d}'.format(inc.victim.name, inc.victim.number), inline=True)
    embed.add_field(name='Offender', value='{:s} - {:d}'.format(inc.offender.name, inc.offender.number), inline=True)

    if inc.lap and inc.lap != '-':
        embed.add_field(name='Lap number', value=inc.lap, inline=False)

    if inc.infringement:
        embed.add_field(name='Infringement', value=inc.infringement, inline=True)

    if inc.outcome:
        embed.add_field(name='Outcome', value=inc.outcome, inline=True)



    return embed



