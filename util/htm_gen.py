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


def _gen_embed_column(field_list: []):

    html = '                     <div>\n'\
           '                       <ul class="ulEmbed">\n'\

    for field in field_list:
        html += '                         <li>\n'\
                '                           <h4>{:s}</h4>\n'\
                '                           <p>{:s}</p>\n'\
                '                         </li>\n'\
                '                         <li><br></li>'.format(field.name, field.value)

    html += '                       </ul>\n'\
            '                     </div>\n'

    return html





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


        embed_content = ''
        embed_placeholder = None
        missed_embeds = 0

        for embed in msg.embeds:
            if embed.type == 'rich':
                embed_content = '			<div class="embed">\n'\
                                '  			  <div></div>\n'\
                                '  			    <div>\n'\
                                '    			  <h1>{:s}</h1>\n'\
                                '                 <p>{:s}</p>\n'\
                                '                   <div class="content">\n'.format(embed.title, embed.description)

                fields_left = []
                fields_right = []
                toggle = True

                for field in embed.fields:
                    if toggle:
                        fields_left.append(field)
                        toggle = False
                    else:
                        fields_right.append(field)
                        toggle = True


                embed_content += _gen_embed_column(fields_left)
                embed_content += _gen_embed_column(fields_right)


                embed_content += '                   </ul>\n'\
                                 '                 </div>\n'\
                                 '               </div>\n'\
                                 '             </div>'


            else:
                missed_embeds += 1


        if missed_embeds != 0:
            embed_placeholder = '[{:d} embed(s) not displayed]'.format(len(msg.embeds))



        img_content = ''
        attach_placeholder = None
        missed_attachments = 0

        for img in msg.attachments:
            # an attachment could be any file supported by discord
            # but base64 embed is currently only used for jpg/png
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

        if img_content != '':
            template += img_content

        if embed_content != '':
            template += embed_content

        if embed_placeholder:
            template += '           <p>{:s}</p>\n'.format(embed_placeholder)

        if attach_placeholder:
            template += '           <p>{:s}</p>\n'.format(attach_placeholder)


        template += '       </div>'\
                    '\n'\
                    '       <div class="time row">\n'\
                    '           <p>{:s}</br>\n'\
                    '           {:s}</p>\n'\
                    '       </div>\n'\
                    '   </div>\n\n'.format(time_hour_str, time_day_str)



    template += ' </body>\n</html>\n'


    return template

