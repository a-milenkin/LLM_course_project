import _io
import asyncio
import datetime
import glob
import io
import logging
from copy import copy

import numpy as np
from PIL import Image
from pydub import AudioSegment
from telebot import formatting
from telebot import types
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.types import Message, CallbackQuery

from models.app import App
from routes.english_tips import phrase2start
from routes.texts import get_start_texts, help_message, after_set_up_role_text, failed_create_role_text
from utils.callback_factories import RolesCallbackData
from utils.callback_factories import SuggestCallbackData
from utils.functions import pop_from_dict
from utils.gpt import voice_chat, text_to_voice_with_duration
from utils.markups import create_conv_reply_markup, create_start_suggests_reply_markup
from utils.message_reactions import set_message_reaction
from utils.structures import UserData
from utils.text_utils import is_english, markdown_escaped

logger = logging.getLogger(__name__)

USER_NOT_IN_GROUP_STATUSES = ('left', 'user not found', "banned")

async def _user_in_group(message: Message, bot: AsyncTeleBot) -> bool:
    membership_settings = App()["config"]["settings"]["membership_check"]
    if not membership_settings["enabled"]:
        return True
    data = await App().Dao.user.find_by_user_id(message.chat.id)
    user = UserData(**data)
    if user.last_generation_date != datetime.datetime.combine(datetime.datetime.now(), datetime.time.min):
        await App().Dao.user.reset_today_generations(message.chat.id)
        user.today_generations = 0
    logger.info(f"User id: {message.chat.id} today generations: {user.today_generations}")

    required_groups = []
    for limitation, required_groups in reversed(membership_settings["daily_requests_subscription_limitations"].items()):
        if user.today_generations >= int(limitation):
            break

    groups_sub_kb = InlineKeyboardMarkup()
    need_to_sub_group_names = []
    for group in required_groups:
        membership = await bot.get_chat_member(group["group_id"], message.chat.id)
        logger.info(f"User id: {message.chat.id}  channel: {group['group_id']} status: {membership.status}")
        if membership.status in USER_NOT_IN_GROUP_STATUSES:
            groups_sub_kb.add(
                InlineKeyboardButton("Вступить в группу", url=group["group_url"])
            )
            need_to_sub_group_names.append(group["group_name"])

    if need_to_sub_group_names:
        need_to_sub_group_names_str = "\n".join(need_to_sub_group_names)
        await bot.send_message(message.chat.id, f"Чтобы получить больше генераций на сегодня, нужно подписаться на \n"
                                                f"{need_to_sub_group_names_str}",
                               reply_markup=groups_sub_kb)
        return False
    return True


async def send_welcome(message: Message, bot: AsyncTeleBot):

    user_id = message.from_user.id
    is_new = False

    if user_id not in App()["known_users"]:
        await App().Dao.user.create({"user_id": user_id,
                                     "username": message.from_user.username,
                                     "generations": 0,
                                     "today_generations": 0,
                                     "last_generation_date": datetime.datetime.combine(datetime.datetime.now(),
                                                                                       datetime.time.min),
                                     "messages": [],
                                     "bot_state": "conversation",
                                     "first_message_index": 0,
                                     "temp_data": {},
                                     "email": None,
                                   
                    
                                     })
        App()["known_users"].add(user_id)

        path = '/src/assets/welcome_msg_photos/onboarding.gif.mp4'
        with open(path, 'rb') as video:
            await bot.send_video(message.chat.id, video)

        is_new = True
        name = f', {message.from_user.first_name}' if len(message.from_user.first_name) > 2 else ''

    else:
        data = await App().Dao.user.find_by_user_id(message.from_user.id)
        user = UserData(**data)
        await App().Dao.user.update({
            "user_id": message.from_user.id,
            "first_message_index": len(user.messages),
            # "utm_campaign" : unique_code,
            "temp_data": await pop_from_dict(user.temp_data, ['hints', 'transcript_in_ru', 'suggest', 'suggest_id']),
            "bot_state": "conversation"
        })

        name = f', {message.from_user.first_name}' if len(message.from_user.first_name) > 2 else ''

    data = await App().Dao.user.find_by_user_id(message.from_user.id)
    user = UserData(**data)

    start_text0, *msg_list = get_start_texts(name, is_new)

    time_gap = 0.5

    bot_msg = await bot.send_message(text=start_text0, chat_id=message.chat.id, parse_mode='HTML')
    for msg in msg_list:
        await asyncio.sleep(time_gap)
        bot_msg = await bot.edit_message_text(text=msg,
                                              chat_id=message.chat.id,
                                              message_id=bot_msg.message_id,
                                              parse_mode='HTML')

    name = f'{message.from_user.first_name}, ' if len(message.from_user.first_name) > 2 else ''
    question = np.random.choice(phrase2start)
    response_text = f'{name}Let’s start! 🚀\n\n{question}'

    await bot.send_message(text=response_text, chat_id=message.chat.id)


    voice_bytesio, voice_duration = await text_to_voice_with_duration(response_text)
    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")



    voice_message = await bot.send_voice(
        voice=voice_bytesio,
        chat_id=message.chat.id,
        reply_markup=create_conv_reply_markup()
    )

    await App().Dao.user.update({
        "user_id": message.from_user.id,
        "messages": [*user.messages, {"role": "assistant",
                                      "content": response_text,
                                      "voice_file_id": voice_message.voice.file_id,
                                      "voice_duration": voice_duration,
                                      "created_at": datetime.datetime.now()}],
        "bot_state": "conversation",
        "bot_role": question
    })

    return voice_message

    # TODO использовать response_duration в сообщении - мол, вай, круто, но давай еще более длинные голосовые записывай!




async def start_conversation_callback(call: CallbackQuery, bot: AsyncTeleBot):
    data = await App().Dao.user.find_by_user_id(call.from_user.id)
    user = UserData(**data)
    if App().Tasks.get(user.user_id):
        App().Tasks[user.user_id].cancel()

    suggest = SuggestCallbackData.parse_and_destroy(call.data)["suggest"]
    temp_data = user.temp_data
    await bot.delete_message(
        call.message.chat.id,
        call.message.id,
    )

    await bot.send_chat_action(chat_id=call.message.chat.id, action="record_voice")

    if len(user.messages) > 1:
        markup = create_conv_reply_markup()
    else:
        markup = create_start_suggests_reply_markup()

    voice_audio, _ = await text_to_voice_with_duration(suggest)
    response_message = await bot.send_voice(
        voice=voice_audio,
        chat_id=call.message.chat.id,
        reply_markup=markup
    )
    temp_data["suggest"] = suggest
    temp_data["suggest_id"] = response_message.message_id
    await App().Dao.user.update(
        {
            "user_id": user.user_id,
            "temp_data": temp_data
        }
    )

    return response_message



async def number_of_text_messages_in_current_dialog(user_id):
    data = await App().Dao.user.find_by_user_id(user_id)
    user = UserData(**data)
    current_dialog = user.messages[user.first_message_index:]
    user_text_messages = [m for m in current_dialog if m["role"] == "user" and not m["voice_file_id"]]
    return len(user_text_messages)


def text_messages_warning(handler_func):
    """
    we want the user to have a conversation using mostly voice messages
    this decorator sends a warning to the user when they send a text message
    """
    TEXT_MESSAGES_ALLOWED = 3
    FIRST_WARNING = (
        "❗️ Если не практиковаться - ничему не научишься! Сейчас я отвечу на твое сообщение, но мне больше нравится слушать твои голосовые 🥰\n\n"
        "В этом диалоге можешь еще отправить мне текстовых сообщений: {text_messages_available}")
    SECOND_WARNING = (
        "❗️ Давай попрактикуем разговор? Я верю, что у тебя была веская причина записать это сообщение текстом, а не голосом, поэтому я отвечу на него, но в следующий раз, пожалуйста, запиши мне голосовое 🥰\n\n"
        "В этом диалоге можешь еще отправить мне текстовых сообщений: {text_messages_available}")
    THIRD_WARNING = (
        "❗️ Пришла пора признаться. Мои процессоры сгорают от текстовых сообщений 😓 Но так уж и быть, из последних сил постараюсь ответить. В следующий раз, пожалуйста, говори только голосом, или сотри мне память при помощи /start\n\n"
        "В этом диалоге можешь еще отправить мне текстовых сообщений: {text_messages_available}")
    FINAL_ERROR = "❗️ Бот просил вам передать, что все его текстовые процессоры сгорели. Можете продолжить общаться голосом, или создать новый диалог при помощи /start"

    async def wrapper(message: Message, bot: AsyncTeleBot):
        if message.content_type == "text":
            n_text_messages = await number_of_text_messages_in_current_dialog(message.from_user.id)
            if n_text_messages == 0:
                await bot.send_message(
                    text=FIRST_WARNING.format(text_messages_available=TEXT_MESSAGES_ALLOWED - n_text_messages - 1),
                    chat_id=message.chat.id
                )
                return await handler_func(message, bot)
            elif n_text_messages == 1:
                await bot.send_message(
                    text=SECOND_WARNING.format(text_messages_available=TEXT_MESSAGES_ALLOWED - n_text_messages - 1),
                    chat_id=message.chat.id
                )
                return await handler_func(message, bot)
            elif n_text_messages == 2:
                await bot.send_message(
                    text=THIRD_WARNING.format(text_messages_available=TEXT_MESSAGES_ALLOWED - n_text_messages - 1),
                    chat_id=message.chat.id
                )
                return await handler_func(message, bot)
            else:
                await bot.send_message(text=FINAL_ERROR, chat_id=message.chat.id)
                return
        else:
            return await handler_func(message, bot)

    return wrapper


# @text_messages_warning
async def voice_handler(message: Message, bot: AsyncTeleBot):
    data = await App().Dao.user.find_by_user_id(message.from_user.id)
    user = UserData(**data)

    # if App().Tasks.get(user.user_id):
        # App().Tasks[user.user_id].cancel()

    input_msg = None
    input_voice_id = None
    input_duration = 10  # default duration for text messages

    if message.content_type == "voice":
        input_voice_id = message.voice.file_id
        voice = await bot.get_file(input_voice_id)
        downloaded_file = await bot.download_file(voice.file_path)
        voice_bytesio = io.BytesIO(downloaded_file)
        voice_bytesio.name = 'voice.mp3'
        input_msg = voice_bytesio
        ogg_audio = AudioSegment.from_file(voice_bytesio, format="ogg")
        input_duration = len(ogg_audio) / 1000
    elif message.content_type == "text":
        input_msg = message.text

    if input_duration >= 5:
        emj = np.random.choice(list('👍👌🤔💋🥰🤗❤️‍🔥😊☺️ '))
        await set_message_reaction(
            App()['config']['bot']['token'],
            message.chat.id,
            message.id,
            emj
        )

    response_text, input_text, tokens_count = await voice_chat(message, input_msg)
    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
    response_voice_audio, response_duration = await text_to_voice_with_duration(response_text)
    response_voice_message = await bot.send_voice(
        voice=response_voice_audio,
        chat_id=message.chat.id,
        reply_markup=create_conv_reply_markup()
    )
    await bot.send_message(
        chat_id=message.chat.id,
        text=f'🎙 ||{markdown_escaped(response_text)}||',
        # text=markdown_escaped(response_text),
        parse_mode='MarkdownV2'
    )

    await App().Dao.user.update(
        {
            "user_id": message.from_user.id,
            "messages": [
                *user.messages,
                {"role": "user", "content": input_text, "voice_file_id": input_voice_id,
                 "voice_duration": input_duration, "created_at": datetime.datetime.now(),
                 "tokens": tokens_count},
                {"role": "assistant", "content": response_text,
                 "voice_file_id": response_voice_message.voice.file_id, "voice_duration": response_duration,
                 "created_at": datetime.datetime.now()}
            ]
            ,
            "temp_data": await pop_from_dict(user.temp_data, ['hints', 'transcript_in_ru', 'suggest', 'suggest_id'])
        }
    )

    return response_voice_message


async def not_conv_voice(message: Message, bot: AsyncTeleBot):
    not_conv_alert = ('If you want to start a new session please send /start.\n\n'
                      'Если Вы хотите начать новую сессию нажмите /start')
    await bot.send_message(text=not_conv_alert, chat_id=message.chat.id)


async def is_user_subscribed(user: UserData):
    membership_settings = App()["config"]["settings"]["membership_check"]
    if membership_settings["enabled"]:
        membership = await App().Bot.get_chat_member(membership_settings["group_id"], user.user_id)
        if membership.status in USER_NOT_IN_GROUP_STATUSES:
            return False
        else:
            return True
    else:
        return True


async def daily_limit(user: UserData):
    NOT_SUBSCRIBED_LIMIT = 3
    SUBSCRIBED_LIMIT = 50
    if user.subscription == "free":
        if await is_user_subscribed(user):
            return SUBSCRIBED_LIMIT
        else:
            return NOT_SUBSCRIBED_LIMIT
    elif user.subscription == "premium":
        return 100


async def daily_limit_minutes(user: UserData):
    """
    returns daily limit in minutes for the user
    """
    NOT_SUBSCRIBED_LIMIT = 3
    SUBSCRIBED_LIMIT = 5
    if user.subscription == "free":
        if await is_user_subscribed(user):
            return SUBSCRIBED_LIMIT
        else:
            return NOT_SUBSCRIBED_LIMIT
    elif user.subscription == "premium":
        return 100


async def send_help(message: Message, bot: AsyncTeleBot):

    help_text = help_message

    await bot.send_message(text=help_text, chat_id=message.chat.id)
