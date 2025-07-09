import asyncio
import datetime
import logging
import nest_asyncio
from aiohttp import web
from check_handler import check_subscriptions
from info_handler import info_command, info_callback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()  # только вывод в Render Logs
    ]
)

import os
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
GROUP_ID = os.getenv("GROUP_ID")

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
        [InlineKeyboardButton("🤸‍♀️ Полина", callback_data=f"polina|{group_id}")],
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

    elif action == "polina":
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=group["thread_id"],
            text=(
                f"Доброго всем утра! Занятие по расписанию в {group['time']}!\n"
                f"Тренировку сегодня проведёт Полина @Polina_NhaTrang_stretching🤸‍♂\n"
                f"Прошу отметиться комментарием или лайком, кто будет на занятии!🌟"
            )
        )
        await query.edit_message_text("Напоминание о тренировке с Полиной отправлено ✅")

    elif action == "skip":
        await query.edit_message_text("Хорошо, ничего не публикуем.")
    pass
    
async def scheduler(app):
    await asyncio.sleep(30)  # даём Render время на перезапуск
    last_check = None

    while True:
        try:
            now_utc = datetime.datetime.utcnow()
            now = now_utc + datetime.timedelta(hours=7)
            weekday = now.strftime("%A")
            current_time = now.strftime("%H:%M")

            print(f"[scheduler] Сейчас {current_time} {weekday}")

            # Проверяем только если это будний день из расписания
            if now.hour == 11 and 1 <= now.minute <= 3:
                if last_check != now.date():
                    print("[scheduler] Время для опроса — запускаем")
                    for idx, group in enumerate(groups):
                        if weekday in group["days"]:
                            await ask_admin(app, idx, group)
                    last_check = now.date()
                else:
                    print("[scheduler] Уже запускали сегодня")
            await asyncio.sleep(20)

        except Exception as e:
            print(f"[scheduler] Ошибка: {e}")
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
    
    app.add_handler(CommandHandler("check", check_subscriptions))
    app.add_handler(CommandHandler("info", info_command))
    
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^(yes|no|reason|skip|polina)\|"))
    app.add_handler(CallbackQueryHandler(info_callback, pattern="^info\|"))
    
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
