import asyncio
import datetime
import logging
import nest_asyncio
from aiohttp import web
from scheduler_handler import scheduler, handle_callback
from start_handler import get_start_handler
from check_handler import check_subscriptions, expired_command
from info_handler import info_command, info_callback
from reminder_handler import handle_poll_answer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    PollAnswerHandler
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()  # только вывод в Render Logs
    ]
)

async def error_handler(update, context):
    logging.error(f"❗ Ошибка: {context.error}")
    
import os
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ADMIN_ID = os.getenv("ADMIN_ID")
# GROUP_ID = os.getenv("GROUP_ID")

# Простенький aiohttp сервер для пинга uptime robot
async def handle_ping(request):
    return web.Response(text="I'm alive!")

async def start_webserver(app):
    from telegram import Update

    async def webhook_handler(request):
        try:
            data = await request.json()
            update = Update.de_json(data, app.bot)
            await app.process_update(update)
        except Exception as e:
            logging.error(f"Ошибка при обработке webhook: {e}")
        return web.Response()

    web_app = web.Application()
    web_app.router.add_get("/", handle_ping)
    web_app.router.add_post("/webhook", webhook_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("🌐 Веб-сервер запущен")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
        
    # Инициализация приложения
    await app.initialize()

    # Хендлеры
    app.add_handler(get_start_handler())
    app.add_handler(CommandHandler("check", check_subscriptions))
    app.add_handler(CommandHandler("expired", expired_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^(yes|skip)\|"))
    app.add_handler(CallbackQueryHandler(info_callback, pattern="^info\|"))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(error_handler)

    # Планировщик и сервер
    asyncio.create_task(scheduler(app))
    await start_webserver(app)

    logging.info("🚀 Бот работает в режиме Webhook")

    # 👉 Устанавливаем webhook
    await app.bot.set_webhook(f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook")
    logging.info("✅ Webhook установлен")

    # Удерживаем процесс
    await asyncio.Event().wait()

# Точка входа
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())


