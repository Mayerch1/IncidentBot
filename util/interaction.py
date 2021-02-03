import discord
import asyncio

from lib.tinyConnector import TinyConnector
from util.verboseErrors import VerboseErrors


def _num_to_emoji(num: int):
    """convert a single digit to the numbers emoji

    Arguments:
        num {int}

    Returns:
        [str] -- unicode emoji, * if num was out of range [0..9]
    """
    if num == 1:
        return '1️⃣'
    elif num == 2:
        return '2️⃣'
    elif num == 3:
        return '3️⃣'
    elif num == 4:
        return '4️⃣'
    elif num == 5:
        return '5️⃣'
    elif num == 6:
        return '6️⃣'
    elif num == 7:
        return '7️⃣'
    elif num == 8:
        return '8️⃣'
    elif num == 9:
        return '9️⃣'
    elif num == 0:
        return '0️⃣'
    else:
        return '*️⃣'

def _emoji_to_num(emoji):
    """convert unicode emoji back to integer

    Arguments:
        emoji {str} -- unicode to convert back, only supports single digits

    Returns:
        [int] -- number of emoji, None if emoji was not a number
    """
    if emoji == '1️⃣':
        return 1
    elif emoji == '2️⃣':
        return 2
    elif emoji == '3️⃣':
        return 3
    elif emoji == '4️⃣':
        return 4
    elif emoji == '5️⃣':
        return 5
    elif emoji == '6️⃣':
        return 6
    elif emoji == '7️⃣':
        return 7
    elif emoji == '8️⃣':
        return 8
    elif emoji == '9️⃣':
        return 9
    elif emoji == '0️⃣':
        return 0
    else:
        return None



async def ack_message(message: discord.Message):
    """Acknowledge the message to the user
    * DELETES the msg, when cleanup and when good permission
    * if not, add green-hook reaction, when good permission
    * if not, answer 'Ok'

    Args:
        message (discord.Message): message, could be deleted afterwards
    """

    server = TinyConnector.get_guild(message.guild.id)

    if VerboseErrors.can_react(message.channel):
        await message.add_reaction('✅')  # green hook
    else:
        await message.channel.send('Ok')



# return chosen index
# max 9 entries
# entry of choice_list must contain .name element
async def reaction_choice(cmd, choice_list: [], out_channel: discord.TextChannel, custom_print=None, timeout = 15):
    """present the content of the list to the user, user can react to it with discord reactions,
       can only show 8 options (1..9)

    Arguments:
        cmd {ctx} -- command context
        choice_list {[]} -- list of user options
        out_channel {discord.TextChannel} -- print out the question/reactions

    Keyword Arguments:
        custom_print {function} -- use this function to print single members of choice_list, takes instance of choice_list[i] as single argument
                                   if None, tries to use choice_list[i].name for print

    Returns:
        [int] -- chosen index, can be None on error
    """
    option_str = 'Choose from the following\n'
    # iteration with i needed for reaction
    for i in range(0, len(choice_list)):
        if custom_print is None:
            element_name = choice_list[i].name
        else:
            element_name = custom_print(choice_list[i])

        option_str += '{:s} {:s}\n'.format(_num_to_emoji(i+1), element_name)

    question = await out_channel.send(option_str)

    for i in range(0, len(choice_list)):
        try:
            await question.add_reaction(_num_to_emoji(i+1))
        except discord.errors.Forbidden:
            # pre-check should be done by caller, but is not guaranteed to be done
            await VerboseErrors.forbidden(None, discord.Permissions(add_reactions=True, read_message_history=True), out_channel)
            return None # abort

    def check(reaction, user):
        return user == cmd.author

    try:
        reaction = await cmd.bot.wait_for('reaction_add', check=check, timeout=timeout)
    except asyncio.exceptions.TimeoutError:
        await question.add_reaction('⏲')
        return None
    else:
        index = _emoji_to_num(reaction[0].emoji)

        if index:
            return choice_list[index-1]
        else:
            return None
    finally:
        try:
            await question.delete()
        except discord.errors.Forbidden:
            pass # silent failure





async def wait_confirm_deny(client, message: discord.Message, timeout, author):
    """Add green hook and cross to message. Wait for user reaction.
       Make sure to differentiate return for False/None

    Args:
        client ([type]): bot client
        message (discord.Message): message for reaction
        timeout ([type]):
        author ([type]): user which is allowed to react

    Return:
        true if hook  selected,
        false if cross selected,
        None if timeout
    """

    reaction = await get_client_reaction(client, message, timeout, author, ['✅', '❌'])

    if reaction == '✅':
        return True
    elif reaction == '❌':
        return False
    else:
        return None



async def get_client_reaction(client, message: discord.Message, timeout, author = None, emoji_whitelist = []):
    """Add all reactions in whitelist to message.
       Returns the first reaction the author reacts with.
       Removes all reactions after user has reacted.

    Args:
        client ([type]): bot client
        message (discord.Message): message for reaction
        timeout ([type]):
        author ([type]): user which is allowed to react
        emoji_whitelist: all accepted emojis, if empty allow all emojis

    Return:
        the reacted emoji
        None if timeout
    """

    def check(reaction, user):
        if ((not emoji_whitelist) or  reaction.emoji in emoji_whitelist) \
            and reaction.message.channel.id == message.channel.id and user.id == author.id:

            return True
        return False

    for emoji in emoji_whitelist:
        try:
            await message.add_reaction(emoji)
        except discord.errors.HTTPException:
            continue


    try:
        reaction = await client.wait_for('reaction_add', check=check, timeout=timeout)
    except asyncio.exceptions.TimeoutError:
        for emoji in emoji_whitelist:
            try:
                await message.remove_reaction(emoji, client.user)
            except discord.errors.HTTPException:
                continue


        await message.add_reaction('⏲')
        return None


    for emoji in emoji_whitelist:
        if emoji != reaction[0].emoji:
            try:
                await message.remove_reaction(emoji, client.user)
            except discord.errors.HTTPException:
                continue

    return reaction[0].emoji


async def get_client_response(client, message: discord.Message, timeout, author = None, validation_fnc = None):
    """Wait for user input into channel of message
       waits until a message is received which fullfills validation_fnc

    Args:
        client ([type]): bot client
        message (discord.Message): only channel of this message is allowed
        timeout ([type]): timeout before None is returned
        author ([type]): author of message, if None accecpt everyone
        validation_fnc ([type], optional): function only returns when this is fullfilled (or timeout). Defaults to None
    """
    def check(m):
        if author is None:
            return m.channel.id == message.channel.id and m.author.id != client.user.id
        else:
            return m.channel.id == message.channel.id and m.author == author


    answer_accepted = False
    while not answer_accepted:
        try:
            reaction = await client.wait_for('message', check=check, timeout=timeout)
        except asyncio.exceptions.TimeoutError:
            await message.add_reaction('⏲') # timer clock
            return None
        else:
            # check against validation_fnc, if given
            answer = reaction.content
            if validation_fnc is not None:
                answer_accepted = validation_fnc(answer)
                if not answer_accepted:
                    await message.channel.send('Invalid format, try again')
            else:
                answer_accepted = True

    return answer


async def guess_target_voice(cmd, name: str, channels: list, err_out: discord.TextChannel, interactive: bool):
    """try to guess a voice channel on the server based on the inputed search term

    Arguments:
        cmd {ctx} -- command context
        name {str} -- search tearm/filter
        channels {list} -- list of all channels of the server
        err_out {discord.TextChannel} -- output error messages (like mismatch)
        interactive {bool} -- if True, ask user to choose from option list when multiple hits

    Returns:
        [discord.VoiceChannel] -- chosen voiceChannel, can be None
    """
    return await _guess_target_object(cmd, name, channels, err_out, discord.VoiceChannel, interactive)


async def guess_target_text(cmd, name: str, channels: list, err_out: discord.TextChannel, interactive: bool):
    """try to guess a text channel on the server based on the inputed search term

    Arguments:
        cmd {ctx} -- command context
        name {str} -- search tearm/filter
        channels {list} -- list of all channels of the server
        err_out {discord.TextChannel} -- output error messages (like mismatch)
        interactive {bool} -- if True, ask user to choose from option list when multiple hits

    Returns:
        [discord.TextChannel] -- chosen TextChannel, can be None
    """

    return await _guess_target_object(cmd, name, channels, err_out, discord.TextChannel, interactive)


async def guess_target_section(cmd, name: str, channels: list, err_out: discord.TextChannel, interactive: bool = False):
    """try to guess a category section on the server based on the inputed search term

    Arguments:
        cmd {ctx} -- command context
        name {str} -- search tearm/filter
        channels {list} -- list of all channels of the server
        err_out {discord.TextChannel} -- output error messages (like mismatch)
        interactive {bool} -- if True, ask user to choose from option list when multiple hits

    Returns:
        [discord.CategoryChannel] -- chosen CategoryChannel, can be None
    """
    return await _guess_target_object(cmd, name, channels, err_out, discord.CategoryChannel, interactive)




async def _guess_target_object(cmd, name: str, channels: list, err_out: discord.TextChannel, target_object, interactive):
    name = name.lower()
    # eliminate text channels
    channels = list(filter(lambda ch: isinstance(ch, target_object), channels))


    # first try exact match
    valid_chans = list(filter(lambda ch: ch.name.lower() == name, channels))

    # direct match was found
    if valid_chans and len(valid_chans) == 1:
        return valid_chans[0]


    if len(valid_chans) > 1:
        await err_out.send('The name "{:s}" is ambiguos ({:d} matches)'.format(name, len(valid_chans)))
        return None


    # try to guess the channel by respecting the first characters
    # first filter all channels smaller/equal to length of filter (exact match failed before)
    valid_chans = list(filter(lambda ch: len(ch.name) > len(name), channels))

    # get all channels which match  (name.*)
    valid_chans = list(filter(lambda ch: ch.name.lower().startswith(name), valid_chans))


    if len(valid_chans) > 1:
        if interactive and len(valid_chans) <= 9:
            # show user his choices
            return await reaction_choice(cmd, valid_chans, err_out)
        else:
            await err_out.send('The search "{:s}" yields {:d} matches. Increase filter length'.format(name, len(valid_chans)))
            return None

    elif not valid_chans:
        await err_out.send('No channel is starting with "{:s}"'.format(name))
        return None


    return valid_chans[0]
