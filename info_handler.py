from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging
import os

# Кнопки
def get_info_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Цены на групповые занятия", callback_data="info|prices")],
        [InlineKeyboardButton("📅 Расписание", callback_data="info|schedule")],
        [InlineKeyboardButton("📍 Как найти зал", callback_data="info|location")],
        [InlineKeyboardButton("🧦 Правила", callback_data="info|rules")],
        [InlineKeyboardButton("🧾 Абонементы", callback_data="info|abonement")],
        [InlineKeyboardButton("🎯 Индивидуальные тренировки", callback_data="info|personal")],
        [InlineKeyboardButton("🤸🏻‍♂️ Про тренеров", callback_data="info|coaches")],
    ])

# /info
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    log_msg = f"/info used by {user.full_name} (@{user.username}) [ID: {user.id}]"
    print(log_msg)
    logging.info(log_msg)

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
    user_id = user.id
    log_msg = f"/info button clicked by {user.full_name} (@{user.username}): {section}"
    print(log_msg)
    logging.info(log_msg)

    karina_id = os.getenv("KARINA_ID")
    if karina_id:
        try:
            await context.bot.send_message(
                chat_id=karina_id,
                text=f"🔘 /info кнопка: {section}\nот {user.full_name} (@{user.username})[ID: {user_id}]"
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить Карине сообщение: {e}")
    # Назад в меню
    if section == "back":
        await query.edit_message_text(
            "📋 Выберите интересующий раздел:",
            reply_markup=get_info_keyboard()
        )
        return

    info_texts = {
        "prices": (
            "📌 *Цены на групповые занятия:*\n\n"
            "▫️ Пробное занятие — 150.000₫\n"
            "▫️ Разовое занятие — 250.000₫\n"
            "▫️ Абонемент на 8 занятий — 1.600.000₫ (по 200.000₫ за занятие)\n\n"
            "❗️*Обязательна предварительная запись*❗️\n\n"
            "❗️*С Мая Новые Абонементы & Правила!*❗️"
        ),
        "schedule": (
            "📅 *Расписание групповых занятий:*\n\n"
            "👧 Младшая группа (4–5 лет)\n"
            "Вт / Чт — 17:15–18:15\n\n"
            "👧 NEW Младшая группа (4–5 лет)\n"
            "Вт / Чт — 18:30–19:30\n\n"
            "[Телеграм Чат Младших групп (4-5 лет)](https://t.me/+lpifqVvxT3YwZGU0)\n\n"
            "🤸🏼 Старшая начинающая (6–9 лет)\n"
            "Пн / Ср / Пт — 17:15–18:15\n\n"
            "🤸🏻‍♀️ Старшая продолжающая (6–10 лет)\n"
            "Пн / Ср / Пт — 18:30–19:30\n\n"
            "[Телеграм Чат Старших групп (6-9 лет)](https://t.me/+lZP99Tb65yljMDUy)\n\n"
            "Взрослая группа\n"
            "Вт / Чт — 10:00–11:00\n\n"
            "[Телеграм Чат группы для Взрослых](https://t.me/+gfVKU9KWBAwwMDc6)\n\n"
            "❗️*Обязательна предварительная запись*❗️"
        ),
            "location": (
                "📍 *Как найти зал:*\n\n"
                "Город: Nha Trang\n"
                "Рядом:Scenia Bay, Shama book bakery, Marisan\n\n"
                "🗺 [Открыть в Google Maps](https://maps.app.goo.gl/PzUYSZNyid4P2gwd7?g_st=com.google.maps.preview.copy)\n"
                "📸 Фото фасада зала:\n" 
                "↳[Чат 4-5 лет](https://t.me/c/3757833438/1/19 )\n"
                "↳[Чат 6-9 лет](https://t.me/c/1820363527/1/2747)\n"
                "↳[Чат Взрослой группы](https://t.me/c/3963339870/8)"
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
            "Проверить абонемент: /check в @acro\\_reminder\\_bot \n\n"
            "❗️*С Мая Новые Абонементы & Правила!*❗️"
        ),
        "personal": (
            "🎯 *Индивидуальные и парные тренировки*\n\n"
            "Для тех, кто ставит конкретные цели, хочет двигаться в своём ритме или проработать элементы.\n\n"
            "👤 *Индивидуально:*\n"
            "• 1 час — 600.000₫\n"
            "• 1.5 часа — 900.000₫\n\n"
            "👥 *В паре (цена за одного):*\n"
            "• 1 час — 400.000₫\n"
            "• 1.5 часа — 500.000₫"
        ),
        "coaches": (
            "🤸🏼 *О тренерах:*\n\n"
            "🤸🏻‍♂️ *Главный тренер: Фанис*\n"
            "• Контакт: [@FaniRaf](https://t.me/FaniRaf)\n"
            "• Кандидат в мастера спорта по акробатике\n"
            "• Тренирует детей в Нячанге с 2022 года\n\n"
            "🤸🏻‍♀️ *Второй тренер: Полина*\n"
            "• Контакт: [@Polina_NhaTrang_stretching](https://t.me/Polina_NhaTrang_stretching)\n"
            "• Кандидат в мастера спорта по спортивной гимнастике"
        ),
    }
    
    text = info_texts.get(section)
    if not text:
        text = "Информация не найдена."
    
# ⬅️ Добавим кнопку «Назад»
    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="info|back")]
    ])

    await query.edit_message_text(text + "\n\n", parse_mode="Markdown", reply_markup=back_keyboard)
