import discord
import codecs

import base64
import requests


def _is_member_steward(member, steward_id):
    # cannot determin roles, if the user isn't a member
    if isinstance(member, discord.Member):
        return any(r.id == steward_id for r in member.roles)
    else:
        return False


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
        time_hour_str = msg.created_at.strftime('%H:%M')
        time_day_str = msg.created_at.strftime('%d.%m.%y')

        img_content = None


        if msg.embeds:
            embed_placeholder = '[{:d} embed(s) not displayed]'.format(len(msg.embeds))
        else:
            embed_placeholder = None

        img_content = ''
        attach_placeholder = None
        missed_attachments = 0

        for img in msg.attachments:


            if img.filename.endswith('jpg') or img.filename.endswith('png'):

                img_base64 = base64.b64encode(requests.get(img.proxy_url).content)
                img_content += '           <img alt="" src="data:image/png;base64,{:s}" />\n'.format(img_base64.decode('utf-8'))
            else:
                missed_attachments += 1


        if missed_attachments > 0:
            attach_placeholder = '[{:d} attachment(s) not displayed]'.format(missed_attachments)


        # replace line breakes with separate <p> tags in html
        msg_text = msg.clean_content.replace('\n', "</br>\n           ")
        #msg_text = msg_text.replace('\n', '</p>\n           <p>')


        template += '   <div class="container {:s}">\n'\
                    '       <div class="avatar mr-25">\n'\
                    '           <img class="row imgProfile" src="{:s}" alt="Avatar">\n'\
                    '           <span class="row name">{:s}</span>\n'\
                    '       </div>\n'\
                    '\n'\
                    '       <div class="comments mr-25">\n'\
                    '           <p>{:s}</p>\n'.format(h_type, avatar_url, nickname, msg_text)

        if embed_placeholder:
            template += '           <p>{:s}</p>\n'.format(embed_placeholder)

        if attach_placeholder:
            template += '           <p>{:s}</p>\n'.format(attach_placeholder)

        if img_content != '':
            template += img_content


        template += '       </div>'\
                    '\n'\
                    '       <div class="time row">\n'\
                    '           <p>{:s}</br>\n'\
                    '           {:s}</p>\n'\
                    '       </div>\n'\
                    '   </div>\n\n'.format(time_hour_str, time_day_str)



    template += ' </body>\n</html>\n'


    return template

