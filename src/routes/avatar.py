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
from routes.texts import get_start_texts, help_message
from utils.callback_factories import SuggestCallbackData
from utils.functions import pop_from_dict
from utils.gpt import voice_chat, text_to_voice_with_duration
from utils.markups import create_conv_reply_markup, create_start_suggests_reply_markup
from utils.message_reactions import set_message_reaction
from utils.structures import UserData
from utils.text_utils import is_english, markdown_escaped

logger = logging.getLogger(__name__)

async def send_welcome(message: Message, bot: AsyncTeleBot):

    '''
    Приветственное сообщение. Срабатывает при нажатии кнопки /start
    '''

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



async def send_help(message: Message, bot: AsyncTeleBot):

    help_text = help_message

    await bot.send_message(text=help_text, chat_id=message.chat.id)
