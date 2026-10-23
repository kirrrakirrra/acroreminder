import asyncio
import logging
import os
import time
from collections import deque
import nest_asyncio
from aiohttp import web
from logging_config import configure_logging
from utils import now_local, format_now
from scheduler_handler import scheduler, handle_callback, send_reminder_command
from start_handler import get_start_handler
from check_handler import check_subscriptions, expired_command
from info_handler import info_command, info_callback
from report_handler import report_command
from reminder_handler import handle_poll_answer, restore_poll_to_group, refresh_report_callback, notify_parents_callback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    PollAnswerHandler
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
configure_logging(BOT_TOKEN)

async def error_handler(update, context):
    logging.error(f"❗ Ошибка: {context.error}")
    
# ADMIN_ID = os.getenv("ADMIN_ID")
# GROUP_ID = os.getenv("GROUP_ID")

# Простенький aiohttp сервер для пинга uptime robot
async def handle_ping(request):
    return web.Response(text="I'm alive!")

RECENT_UPDATE_LIMIT = 4096


class WebhookUpdateProcessor:
    """Promptly accept, deduplicate, and track Telegram update tasks."""

    def __init__(self, app, cache_limit=RECENT_UPDATE_LIMIT):
        self.app = app
        self.cache_limit = cache_limit
        self.recent_ids = set()
        self.recent_order = deque()
        self.active_tasks = set()
        self.action_active = set()
        self.action_recent = {}
        self.action_cooldown_seconds = 2.0

    def _accept(self, update_id):
        if update_id in self.recent_ids:
            return False
        self.recent_ids.add(update_id)
        self.recent_order.append(update_id)
        while len(self.recent_order) > self.cache_limit:
            self.recent_ids.discard(self.recent_order.popleft())
        return True

    def _action_key(self, update):
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        if user is None or chat is None:
            return None
        message = getattr(update, "message", None)
        if message and isinstance(getattr(message, "text", None), str):
            command = message.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
            if command in {"/start", "/check", "/info", "/report", "/expired", "/send_reminder"}:
                return (chat.id, user.id, "command", message.text.strip())
        query = getattr(update, "callback_query", None)
        if query and getattr(query, "data", None):
            origin = getattr(query, "message", None)
            return (chat.id, user.id, "callback", getattr(origin, "message_id", None), query.data)
        return None

    async def _process_action(self, update, key):
        try:
            await self.app.process_update(update)
        finally:
            if key is not None:
                self.action_active.discard(key)
                self.action_recent[key] = time.monotonic()

    def _finished(self, task):
        self.active_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logging.error(
                "Background webhook update failed",
                exc_info=(type(task.exception()), task.exception(), task.exception().__traceback__),
            )

    async def handle(self, request):
        try:
            data = await request.json()
            update = Update.de_json(data, self.app.bot)
            if update.update_id is None:
                raise ValueError("Telegram update has no update_id")
        except Exception as exc:
            logging.warning("Malformed webhook request: %s", exc)
            return web.Response(status=400)

        if self._accept(update.update_id):
            key = self._action_key(update)
            now = time.monotonic()
            if len(self.action_recent) > RECENT_UPDATE_LIMIT:
                self.action_recent = {
                    action: timestamp for action, timestamp in self.action_recent.items()
                    if now - timestamp < self.action_cooldown_seconds
                }
            if key is not None and (
                key in self.action_active
                or now - self.action_recent.get(key, float("-inf")) < self.action_cooldown_seconds
            ):
                logging.info("Ignoring rapid duplicate Telegram action update_id=%s", update.update_id)
                query = getattr(update, "callback_query", None)
                if query is not None:
                    try:
                        await query.answer()
                    except TelegramError:
                        logging.debug("Duplicate callback was already acknowledged", exc_info=True)
                return web.Response(status=200)
            if key is not None:
                self.action_active.add(key)
                self.action_recent[key] = now
            task = asyncio.create_task(
                self._process_action(update, key), name=f"telegram-update-{update.update_id}"
            )
            self.active_tasks.add(task)
            task.add_done_callback(self._finished)
        else:
            logging.info("Ignoring duplicate Telegram update_id=%s", update.update_id)
        return web.Response(status=200)


async def start_webserver(app):
    processor = WebhookUpdateProcessor(app)

    web_app = web.Application()
    web_app["telegram_update_processor"] = processor
    web_app.router.add_get("/", handle_ping)
    web_app.router.add_post("/webhook", processor.handle)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("🌐 Веб-сервер запущен")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
        
    # Инициализация приложения
    await app.initialize()

    await asyncio.to_thread(restore_poll_to_group)
    
    # Хендлеры
    app.add_handler(get_start_handler())
    app.add_handler(CommandHandler("check", check_subscriptions))
    app.add_handler(CommandHandler("expired", expired_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("send_reminder", send_reminder_command))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(yes|skip|select_reminder|resend_reminder|cancel_reminder)(?:\||$)"))
    app.add_handler(CallbackQueryHandler(info_callback, pattern=r"^info\|"))
    app.add_handler(CallbackQueryHandler(refresh_report_callback, pattern=r"^refresh_report\|"))
    app.add_handler(CallbackQueryHandler(notify_parents_callback, pattern="^notify_parents\\|"))
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
