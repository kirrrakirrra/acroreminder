import asyncio
import datetime
import logging
import nest_asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler,
)

logging.basicConfig(
    filename="bot.log",  # сохраняет в файл
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
import os
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 986242491
GROUP_ID = -1001820363527

# Список групп
groups = [
    {
        "name": "Старшей начинающей группы",
        "days": ["Monday", "Wednesday", "Friday",],
        "time": "17:15",
        "thread_id": 2225,
    },
    {
        "name": "Старшей продолжающей группы",
        "days": ["Monday", "Wednesday", "Friday",],
        "time": "18:30",
        "thread_id": 7,
    },
    {
        "name": "Младшей группы",
        "days": ["Tuesday", "Thursday",],
        "time": "17:30",
        "thread_id": 2226,
    },
]

pending = {}

cancel_messages = {
    "visa": "Всем доброго дня! 🛂 Сегодня я на визаране, поэтому занятия не будет. Отдохните хорошо, увидимся совсем скоро на тренировке! ☀️",
    "illness": "Всем доброго дня! 🤒 К сожалению, я приболел и не смогу провести сегодняшнее занятие. Надеюсь быстро восстановиться и скоро увидеться с вами! Берегите себя! 🌷",
    "unwell": "Всем доброго дня! 😌 Сегодня, к сожалению, чувствую себя неважно и не смогу провести тренировку. Спасибо за понимание — совсем скоро вернусь с новыми силами! 💪",
    "unexpected": "Всем доброго дня! ⚠️ По непредвиденным обстоятельствам сегодня не смогу провести занятие. Спасибо за понимание, увидимся в следующий раз! 😊",
    "tech": "Всем доброго дня! ⚙️ Сегодня, к сожалению, в зале возникли технические сложности, и мы не сможем провести тренировку. Уже работаем над тем, чтобы всё наладить. До скорой встречи! 🤸‍♀️",
}

def get_decision_keyboard(group_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data=f"yes|{group_id}")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data=f"no|{group_id}")],
        [InlineKeyboardButton("⏭ Нет, но я сам напишу в группу", callback_data=f"skip|{group_id}")],
    ])

def get_reason_keyboard(group_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤒 Болезнь", callback_data=f"reason|{group_id}|illness")],
        [InlineKeyboardButton("🛂 Визаран", callback_data=f"reason|{group_id}|visa")],
        [InlineKeyboardButton("😌 Плохое самочувствие", callback_data=f"reason|{group_id}|unwell")],
        [InlineKeyboardButton("⚠️ Непредвиденное", callback_data=f"reason|{group_id}|unexpected")],
        [InlineKeyboardButton("⚙️ Тех. неполадки", callback_data=f"reason|{group_id}|tech")],
    ])

async def ask_admin(app, group_id, group):
    msg = await app.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Сегодня занятие для {group['name']} в {group['time']} по расписанию?",
        reply_markup=get_decision_keyboard(group_id)
    )
    pending[msg.message_id] = group

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    action = data[0]
    group_id = int(data[1])
    group = groups[group_id]

    if action == "yes":
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=group["thread_id"],
            text=f"Всем доброго дня! Занятие для {group['name']} по расписанию в {group['time']} 🤸🏻🤸🏻‍♀️"
        )
        await query.edit_message_text("Напоминание отправлено ✅")

    elif action == "no":
        await query.edit_message_text("Выберите причину отмены занятия:", reply_markup=get_reason_keyboard(group_id))

    elif action == "reason":
        reason_key = data[2]
        message = cancel_messages.get(reason_key, "Занятие отменяется.")
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=group["thread_id"],
            text=message
        )
        await query.edit_message_text("Отмена опубликована ❌")

    elif action == "skip":
        await query.edit_message_text("Хорошо, ничего не публикуем.")
    pass
async def scheduler(app):
        while True:
            try:
                now_utc = datetime.datetime.utcnow()
                now = now_utc + datetime.timedelta(hours=7)
                if now.hour == 12 and now.minute == 30:
                    weekday = now.strftime("%A")
                    for idx, group in enumerate(groups):
                        if weekday in group["days"]:
                            await ask_admin(app, idx, group)
                    await asyncio.sleep(60)
                await asyncio.sleep(20)
            except Exception as e:
                logging.exception("Ошибка в scheduler")
                await asyncio.sleep(10)

# Простенький aiohttp сервер для пинга uptime robot
async def handle_ping(request):
    return web.Response(text="I'm alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    # без обработки текста, так как не нужна
    asyncio.create_task(scheduler(app))
    asyncio.create_task(start_webserver())  # запускаем веб-сервер параллельно
    await app.run_polling()


if __name__ == "__main__":
    import time
    nest_asyncio.apply()
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            logging.exception("Бот упал с ошибкой. Перезапуск через 5 секунд...")
            time.sleep(5)
