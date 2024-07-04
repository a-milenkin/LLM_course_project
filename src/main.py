import asyncio
import logging
import sys
import datetime

import aiohttp_cors
import sentry_sdk
import telebot
from aiohttp import web
from aiohttp_swagger import setup_swagger
from telebot import asyncio_filters
from telebot.async_telebot import AsyncTeleBot, ExceptionHandler
from telebot.asyncio_storage import StateMemoryStorage
from telebot.types import CallbackQuery, Message

import filters
import routes
import utils
from dao import setup_dao
from filters import KnownUser, Admin
from managers import setup_managers
from models.app import App
from settings import get_config
from utils.structures import UserData

# from migrations.mongo_migrate import migrate_users


async def cancel_any_state(event: CallbackQuery | Message | int, bot: AsyncTeleBot):
    if isinstance(event, CallbackQuery):
        chat_id = event.message.chat.id
    elif isinstance(event, Message):
        chat_id = event.chat.id
    elif isinstance(event, int):
        chat_id = event
    else:
        return
    await bot.delete_state(chat_id)


class RaiseErrorHandler(ExceptionHandler):
    def handle(self, e):
        raise e


class SentryHandler(ExceptionHandler):
    def __init__(self, dsn):
        self.dsn = dsn
        sentry_sdk.init(self.dsn, traces_sample_rate=1.0)

    def handle(self, exception):
        logging.error(exception)
        sentry_sdk.capture_exception(exception)


async def main(argv):
    async def handle_tg_updates(request: web.Request):
        if request.match_info.get('token') != bot.token:
            return web.Response(status=403)
        try:
            request_body_dict = await request.json()
            update = telebot.types.Update.de_json(request_body_dict)
            asyncio.create_task(bot.process_new_updates([update]))
        except Exception as e:
            logging.error(e, exc_info=True)
        return web.Response()

    app = App()
    app.WebApp = web.Application(client_max_size=30 * 1024 ** 2)
    config = get_config(argv)
    app["config"] = config

    POLLING = app["config"]["bot"]["tg_polling"]  # telegram connection parameter. Set False to start with webhook

    await setup_managers(app)
    await setup_dao(app)
    # await setup_tasks(app)
    
    # mongo migrations
    # await migrate_users()
    await app.Dao.user.add_new_field('bot_role', 'english tutor')
    await app.Dao.user.add_new_user_messages_field('tokens', 0)
    
    # mongo migrations
    #await migrate_users()

    app["known_users"] = await app.Dao.user.find_known_users_ids()
    logging.basicConfig(
        level=logging.getLevelName(config["logging_level"]),
        format='%(asctime)s (%(filename)s:%(lineno)d %(threadName)s) %(levelname)s - %(name)s: "%(message)s"',
    )
    telebot.logger.setLevel(logging.getLevelName(config["logging_level"]))
    telebot.logger.handlers = []  # all logs will be handled in logging basic level
    telebot.logger.setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    
    if app["config"]["bot"].get("sentry"):
        exception_handler = SentryHandler(dsn=app["config"]["bot"]["sentry"])
    else:
        exception_handler = RaiseErrorHandler()

    bot = AsyncTeleBot(
        config["bot"]["token"],
        state_storage=StateMemoryStorage(),
        exception_handler=exception_handler,
        # parse_mode="HTML",
    )
    app.Bot = bot

    logging.info(app)

    # webhook/web api install
    # webhook_url = f'{config["bot"]["webhook_base_url"]}/{config["bot"]["token"]}/'
    # app.WebApp.router.add_post('/{token}/', handle_tg_updates)

    async def example_get(request: web.Request):
        print('REQUEST HAPPENED')
        return web.Response()

    app.WebApp.router.add_get('/', example_get)
    setup_swagger(app.WebApp, ui_version=3, swagger_from_file="swagger.yaml", )
    cors = aiohttp_cors.setup(app.WebApp, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })
    # Configure CORS on all routes.
    for route in list(app.WebApp.router.routes()):
        cors.add(route)

    # Remove webhook, it fails sometimes the set if there is a previous webhook
    await bot.remove_webhook()

    if not POLLING:
        logging.info(webhook_url)
        await bot.set_webhook(url=webhook_url)

    bot.add_custom_filter(asyncio_filters.StateFilter(bot))
    bot.add_custom_filter(KnownUser())
    bot.add_custom_filter(Admin())
    await bot.set_my_commands(
        commands=[
            telebot.types.BotCommand("start", "🚀 Начать работу"),
            telebot.types.BotCommand("rating", "🏆 Общий рейтинг "),
            telebot.types.BotCommand("help", "📝 Описание чатбота"),
        ],
    )

    def register_handlers():
        bot.register_message_handler(routes.avatar.send_welcome, commands=["start"], pass_bot=True)
        bot.register_message_handler(routes.rating.get_rating, commands=['rating'], pass_bot=True)
        bot.register_message_handler(routes.avatar.send_help, commands=["help"], pass_bot=True)

        ######## conversation keyboard ########

        bot.register_message_handler(
            routes.conversation_keyboard.get_transcript_en,
            regexp='🇬🇧 Text the same in English',
            bot_state='conversation',
            messages_count='0',
            pass_bot=True
        )
        bot.register_message_handler(
            routes.conversation_keyboard.get_transcript_ru,
            regexp='🇷🇺 Написать то же самое на Русском',
            bot_state='conversation',
            messages_count='0',
            pass_bot=True
        )
        bot.register_message_handler(
            routes.conversation_keyboard.get_hints,
            regexp="🆘 I'm stuck! Hints, please",
            bot_state='conversation',
            pass_bot=True
        )
        bot.register_message_handler(
            routes.conversation_keyboard.finish_conv,
            regexp='🏁 Finish & get feedback',
            bot_state='conversation',
            pass_bot=True
        )
        bot.register_message_handler(
            routes.rating.get_rating,
            regexp='🕰 Сколько я наговорил?',
            bot_state='conversation',
            pass_bot=True
        )


        bot.register_message_handler(
            routes.avatar.voice_handler,
            content_types=["voice", "text"],
            bot_state=["conversation"],
            pass_bot=True
        )


        bot.register_message_handler(
            routes.avatar.not_conv_voice,
            content_types=["voice"],
            bot_state='default',
            pass_bot=True
        )


        ######### callbacks register ##########

        bot.register_callback_query_handler(
            routes.avatar.start_conversation_callback,
            lambda call: call.data.startswith("suggest"),
            pass_bot=True,
        )

        ############### profile ###############

        bot.add_custom_filter(filters.CheckBotState())
        bot.add_custom_filter(filters.CheckMessagesCountMore())

    register_handlers()

    # async def autoextend_loop(bot: AsyncTeleBot):
    #     while True:
    #         await routes.payments.sub_autoextend_all(bot)
    #         # wait 1 day
    #         await asyncio.sleep(60 * 60 * 24)
    #
    # asyncio.create_task(autoextend_loop(app.Bot))



    if POLLING:
        await asyncio.gather(
            bot.polling(non_stop=True),
            web._run_app(
                app.WebApp,
                host="0.0.0.0",
                port=80,
            )
        )
    else:
        await web._run_app(
            app.WebApp,
            host="0.0.0.0",
            port=80,
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
