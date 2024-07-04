import _io
import glob
import io
import os
import time
import uuid
from pathlib import Path
from typing import List

import requests
import tiktoken
import yaml
from langchain.output_parsers import PydanticOutputParser
from langchain.schema import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.pydantic_v1 import BaseModel, Field
# from langchain_openai import ChatOpenAI
from langchain_community.chat_models import ChatOpenAI
# from langchain.chat_models import ChatOpenAI
from openai import AsyncOpenAI
from pydub import AudioSegment
from telebot.types import Message

from models.app import App
from utils.structures import UserData

config_fname = os.environ.get("APP_CONFIG", "config.yaml")
OPENAI_API_KEY = ""
with open(config_fname, encoding='utf-8') as f:
    OPENAI_API_KEY = yaml.safe_load(f.read())["app"]["openai"]["api_key"]

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

class TopicsResponse(BaseModel):
    topic: str = Field(description="A topic for the conversation suitable for life situations")
    main_goal: str = Field(description="The main goal of the conversation according to the topic")
    side_tasks: List[str] = Field(description="list of 3 side tasks")
    options_to_start_the_dialog: List[str] = Field(
        description="5 short variants of the dialog beginning. Each of them MUST be less than 6 words long"
    )

class ConversationSuggests(BaseModel):
    conversation_suggests: List[str] = Field(
        description="5 short variants to continue the conversation. Each of them MUST be less than 6 words long"
    )


async def text_to_voice(text: str) -> str:
    ans_path = os.path.join('..', 'answers', f'{str(uuid.uuid4())}.opus')
    speech_file_path = Path(ans_path)
    response = await openai_client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text
    )
    return response.content


async def text_to_voice_with_duration(text: str):
    response_mp3 = await openai_client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text,
        response_format='mp3'
    )
    mp3_bytesio = io.BytesIO(response_mp3.content)
    mp3_audio = AudioSegment.from_file(mp3_bytesio, format="mp3")
    duration = len(mp3_audio) / 1000  # продолжительность в секундах
    opus_audio: _io.BufferedRandom = mp3_audio.export(format="ogg", codec="libopus")
    return opus_audio, duration


async def get_transcript(audio_file: io.BytesIO) -> str:
    transcript = await openai_client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language="en"
    )
    return transcript.text


async def get_last_transcript(message: Message) -> str:
    data = await App().Dao.user.find_by_user_id(message.from_user.id)
    user = UserData(**data)
    return user.messages[-1]["content"]
    # Возможно использовать context_messages
    # context_messages = user.messages[user.first_message_index:]
    # if len(context_messages) > 0:
    #     return context_messages[-1]["content"]
    #
    # return 'Start a dialog first'


async def get_last_transcript_in_ru(message: Message, model_type: str = 'openai') -> str:
 
    if model_type == 'openai':
        model = ChatOpenAI(temperature=0, openai_api_key=OPENAI_API_KEY)
    else:
        raise ValueError(f"Unknown model type {model_type}, please choose from ['openai']")

    transcript = await get_last_transcript(message)
    # if transcript == 'Start a dialog first':
    #     return 'Start a dialog first'

    data = await App().Dao.user.find_by_user_id(message.from_user.id)
    user = UserData(**data)
    temp_data = user.temp_data

    if temp_data.get('transcript_in_ru'):
        return temp_data['transcript_in_ru']

    messages = [
        SystemMessage(content="You're a professional translator from English to Russian"),
        HumanMessage(content=f"Translate the following English text to Russian: {transcript}")
    ]

    result = model(messages)

    temp_data['transcript_in_ru'] = result.content
    await App().Dao.user.update(
        {
            'user_id': message.from_user.id,
            'temp_data': temp_data
        }
    )

    return result.content


def openai_to_langchain_role(role):
    if role == "assistant":
        return "ai"
    elif role == "user":
        return "human"
    else:
        return role


async def voice_chat(message: Message, audio_or_text: io.BytesIO | str, is_hints: bool = False,
                     model_type: str = 'openai') -> (str, str):
 
    if model_type == 'openai':
        model = ChatOpenAI(temperature=1, openai_api_key=OPENAI_API_KEY)
    else:
        raise ValueError(f"Unknown model type {model_type}, please choose from ['gigachat', 'openai']")

    if isinstance(audio_or_text, str):
        text = audio_or_text
    else:
        text = await get_transcript(audio_or_text) or 'empty message'

    data = await App().Dao.user.find_by_user_id(message.from_user.id)
    user = UserData(**data)

    # Local time has date and time
    t = time.localtime()
    current_time = time.strftime("%H:%M", t)

 
    full_text = ""
    system_text = (
        "You are an assistant for learning English. Your name is Chatodor. "
        f"You live in Telegram. Current time is {current_time} "
        "You have to help develop the skill of "
        "speaking English, so you have to maintain a "
        "dialogue with the student. To noticeably improve speaking and writing skills, "
        "is needed a week of working with the you for 15 minutes a day! "
        "Students can check their progress in /rating. "
        "Always ask a question at the end of the answer! "
        "Always respond with short messages. "
       
    )


    system_text += f"Topic for the conversation will be the {user.bot_role}."


    system_text = (f"{system_text} The user has uploaded their file, use the words and constructions "
                    f"from it in conversation, you should help the user learn the material in that file. "
                    f"Have a conversation about the topic of the file. ")

    raw_prompt = [("system", system_text)]

    for _, content in raw_prompt:
        full_text += content + " "

    raw_prompt.append(MessagesPlaceholder(variable_name="history"))

    context_messages = user.messages[user.first_message_index:]
    # include only last 10 messages
    context_messages = context_messages[:10]

    for msg in context_messages:
        full_text += msg['content'] + " "
    full_text += text

    enc = tiktoken.get_encoding("cl100k_base")
    tokens_count = len(enc.encode(full_text))

    history = [(openai_to_langchain_role(m["role"]), m["content"]) for m in context_messages]

    if is_hints:
        raw_prompt.append(("human", text + "\nAnswer in this format: {format_instructions}"))
        prompt = ChatPromptTemplate.from_messages(raw_prompt)
        parser = PydanticOutputParser(pydantic_object=ConversationSuggests)
        prompt = prompt.partial(format_instructions=parser.get_format_instructions())
        chain = prompt | model | parser
        result = await chain.ainvoke({"history": history})
        return result

    raw_prompt.append(("human", text))
    # print('raw_prompt: ', raw_prompt)
    prompt = ChatPromptTemplate.from_messages(raw_prompt)
    chain = prompt | model
    result = await chain.ainvoke({"history": history})
    return result.content, text, tokens_count


async def get_feedback(user_id, model_type: str = 'openai'):
 
    if model_type == 'openai':
        model = ChatOpenAI(temperature=1, openai_api_key=OPENAI_API_KEY)
    else:
        raise ValueError(f"Unknown model type {model_type}, please choose from [openai]")

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are an assistant for learning English. "
         "You have to help develop the skill of "
         "speaking English, so you have to maintain a "
         "dialogue with the student. Here's my (user) dialogue with you (assistant)"),
        ("human",
         "{history}"),
        ("human",
         "Please provide a detailed feedback on my grammar and vocabulary. "
         "Point out as many growth opportunities as possible. "
         "Reference my messages when providing a feedback.")
    ])
    data = await App().Dao.user.find_by_user_id(user_id)
    user = UserData(**data)
    context_messages = user.messages[user.first_message_index:]
    # include only last 10 messages
    context_messages = context_messages[:10]

    history = "\n".join([f"{m['role']}: {m['content']}" for m in context_messages])
    chain = prompt | model
    result = await chain.ainvoke({"history": history})
    return result.content
