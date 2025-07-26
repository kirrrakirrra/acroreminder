from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging
import os

# Кнопки
def get_info_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Цены", callback_data="info|prices")],
        [InlineKeyboardButton("📅 Расписание", callback_data="info|schedule")],
        [InlineKeyboardButton("🧦 Правила", callback_data="info|rules")],
        [InlineKeyboardButton("🧾 Абонементы", callback_data="info|abonement")],
        [InlineKeyboardButton("🎯 Индивидуальные тренировки", callback_data="info|personal")],
    ])

# /info
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    log_msg = f"/info used by {user.full_name} (@{user.username}) [ID: {user.id}]"
    print(log_msg)
    logging.info(log_msg)

    karina_id = os.getenv("KARINA_ID")
    if karina_id:
        try:
            await context.bot.send_message(
                chat_id=karina_id,
                text=f"📋 /info использовал: {user.full_name} (@{user.username})[ID: {user_id}]"
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить Карине сообщение: {e}")

    await update.message.reply_text(
        "📋 Выберите интересующий раздел:",
        reply_markup=get_info_keyboard()
    )

async def info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("info|"):
        return

    section = data.split("|")[1]
    user = update.effective_user
    log_msg = f"/info button clicked by {user.full_name} (@{user.username}): {section}"
    print(log_msg)
    logging.info(log_msg)

    karina_id = os.getenv("KARINA_ID")
    if karina_id:
        try:
            await context.bot.send_message(
                chat_id=karina_id,
                text=f"🔘 /info кнопка: *{section}*\nот {user.full_name} (@{user.username})",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить Карине сообщение: {e}")

    info_texts = {
        "prices": (
            "📌 *Цены*\n\n"
            "*Групповые занятия:*\n"
            "▫️ Пробное занятие — 150.000₫\n"
            "▫️ Разовое занятие — 250.000₫\n"
            "▫️ Абонемент на 8 занятий — 1.600.000₫ (по 200.000₫ за занятие)\n\n"
            "*Индивидуальные и парные занятия:*\n"
            "▫️ Индивидуальная тренировка — 1ч: 350.000₫, 1.5ч: 500.000₫\n"
            "▫️ Парная тренировка (с человека) — 1ч: 300.000₫, 1.5ч: 400.000₫"
        ),
        "schedule": (
            "📅 *Расписание групповых занятий:*\n\n"
            "👧 Младшая группа (4–5 лет)\n"
            "Вт / Чт — 17:30–18:30\n\n"
            "🤸🏼 Старшая начинающая (6–9 лет)\n"
            "Пн / Ср / Пт — 17:15–18:15\n\n"
            "🤸🏻‍♀️ Старшая продолжающая (6–10 лет)\n"
            "Пн / Ср / Пт — 18:30–19:30"
        ),
        "rules": (
            "🧦 *Правила и подготовка:*\n\n"
            "✅ *Что нужно:*\n"
            "– Майка + шорты или лосины\n"
            "– Бутылка воды\n"
            "– Тренируемся босиком\n\n"
            "🚫 *Что не нужно:*\n"
            "– Игрушки\n"
            "– Еда и сладости"
        ),
        "abonement": (
            "🧾 *Абонементы*\n\n"
            "Абонемент включает *8 занятий* и действует в течение календарного месяца с момента первого посещения.\n\n"
            "Стоимость — 1.600.000₫ (по 200.000₫ за занятие).\n\n"
            "Если вы заранее предупредили об уважительной причине пропуска (болезнь, визаран, важное событие), срок действия может быть продлён.\n\n"
            "Проверить абонемент: /check в @acro_reminder_bot"
        ),
        "personal": (
            "🎯 *Индивидуальные и парные тренировки*\n\n"
            "Для тех, кто ставит конкретные цели, хочет двигаться в своём ритме или проработать элементы.\n\n"
            "👤 *Индивидуально:*\n"
            "• 1 час — 350.000₫\n"
            "• 1.5 часа — 500.000₫\n\n"
            "👥 *В паре (цена за одного):*\n"
            "• 1 час — 300.000₫\n"
            "• 1.5 часа — 400.000₫"
        ),
    }

    text = info_texts.get(section, "Информация не найдена.")
    await query.edit_message_text(text, parse_mode="Markdown")
