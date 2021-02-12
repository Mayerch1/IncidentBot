import discord
import codecs


def _is_member_steward(member, steward_id):
            return any(r.id == steward_id for r in member.roles)


async def gen_html_report(channel, victim_id, offender_id, steward_id, bot_id):

    messages = await channel.history(limit=200).flatten()

    with open('util/template.html', 'r') as t_file:
        template = ' '.join(t_file.readlines())


    for msg in reversed(messages):
        h_type = ' '
        h_class = 'left'

        # victim_id is default values
        if msg.author.id == offender_id:
            h_type = 'offender'
            h_class = 'left'
        elif msg.author.id == bot_id:
            h_type = 'bot'
            h_class = 'right'
        elif _is_member_steward(msg.author, steward_id):
            h_type = 'steward'
            h_class = 'right'


        if msg.author.avatar:
            avatar_url = 'https://cdn.discordapp.com/avatars/{:d}/{:s}.png'.format(msg.author.id, msg.author.avatar)
        else:
            avatar_url = ' '


        nickname = msg.author.display_name
        time_str = msg.created_at.strftime('%H:%M %d.%m.%y')


        if msg.embeds:
            embed_placeholder = '[{:d} embed(s) not displayed]'.format(len(msg.embeds))
        else:
            embed_placeholder = ' '

        if msg.attachments:
            attach_placeholder = '[{:d} attachment(s) not displayed]'.format(len(msg.attachments))
        else:
            attach_placeholder = ' '




        template += '     <div class="container {:s}">\n'\
                    '        <img src="{:s}" alt="Avatar" class="{:s}">\n'\
                    '        <p class="name">{:s}</p>\n'\
                    '        <p>{:s}</p>\n'\
                    '        <p>{:s}</p>\n'\
                    '        <p>{:s}</p>\n'\
                    '        <span class="time-right">{:s}</span>\n'\
                    '     </div>\n\n'.format(h_type, avatar_url, h_class, nickname, msg.clean_content, embed_placeholder, attach_placeholder, time_str)



    template += ' </body>\n</html>\n'


    return template

