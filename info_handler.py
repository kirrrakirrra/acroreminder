from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging
import os

# Кнопки
def get_info_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Цены и абонементы", callback_data="info|prices")],
        [InlineKeyboardButton("📅 Расписание и группы", callback_data="info|schedule")],
        [InlineKeyboardButton("🧾 Правила абонементов", callback_data="info|abonement")],
        [InlineKeyboardButton("📍 Как найти зал", callback_data="info|location")],
        [InlineKeyboardButton("🧦 Правила", callback_data="info|rules")],
        [InlineKeyboardButton("🎯 Индивидуальные тренировки", callback_data="info|personal")],
        [InlineKeyboardButton("🤸🏻‍♂️ Про тренеров", callback_data="info|coaches")],
    ])

def get_group_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👧 4–5 лет", callback_data="info|group_4_5")],
        [InlineKeyboardButton("🤸 6–9 лет", callback_data="info|group_6_9")],
        [InlineKeyboardButton("🧑 Взрослая группа", callback_data="info|group_adults")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="info|back")],
    ])

def get_group_navigation_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Правила абонементов", callback_data="info|abonement")],
        [InlineKeyboardButton("📅 Расписание и группы", callback_data="info|schedule")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="info|back")],
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
            "💳 *Цены и абонементы:*\n\n"
            "▫️ Пробное занятие — 150.000₫\n"
            "▫️ Разовое занятие — 300.000₫\n"
            "▫️ Тестовый абонемент на 3 занятия — 600.000₫ (по 200.000₫ за занятие)\n"
            "   _Только для новичков_\n\n"
            "❗️*Обязательна предварительная запись*❗️\n\n"
            "❗️Пробные и разовые посещения возможны только при наличии мест в группе.\n\n"
            ""
        ),
        "schedule": (
            "📅 *Расписание групповых занятий:*\n\n"
            "👧 *Младшая группа (4–5 лет)*\n"
            "Вт / Чт — 17:15–18:15\n\n"
            "👧 *NEW Младшая группа (4–5 лет)*\n"
            "Вт / Чт — 18:30–19:30\n\n"
            "[Телеграм чат Младших групп (4-5 лет)](https://t.me/+lpifqVvxT3YwZGU0)\n\n"
            "🤸🏼 *Старшая начинающая (6–9 лет)*\n"
            "Пн / Ср / Пт — 17:15–18:15\n\n"
            "🤸🏻‍♀️ *Старшая продолжающая (6–10 лет)*\n"
            "Пн / Ср / Пт — 18:30–19:30\n\n"
            "[Телеграм чат Старших групп (6-9 лет)](https://t.me/+lZP99Tb65yljMDUy)\n\n"
            "🧔‍♂️ *Взрослая группа*\n"
            "Вт / Чт — 10:00–11:00\n\n"
            "[Телеграм чат Взрослой группы](https://t.me/+gfVKU9KWBAwwMDc6)\n\n"
            "❗️*Обязательна предварительная запись*❗️"
        ),
            "location": (
                "📍 *Как найти зал:*\n\n"
                "Город: Nha Trang\n"
                "Рядом:Scenia Bay, Shama book bakery, Marisan\n\n"
                "🗺 [Открыть в Google Maps](https://maps.app.goo.gl/PzUYSZNyid4P2gwd7?g_st=com.google.maps.preview.copy)\n"
                "📸 Фото фасада зала:\n" 
                "↳[Чат 4-5 лет](https://t.me/c/3757833438/1/19)\n"
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
            "🧾 *Правила абонементов*\n\n"
            "⏳ Абонемент действует *35 дней* с момента первого посещения, "
            "но не позднее 7 дней с даты оплаты.\n\n"
            "❗️Неиспользованные занятия не переносятся.\n"
            "❗️Переносы занятий не предусмотрены.\n\n"
            "⏸ Возможна разовая заморозка только в случае форс-мажорных обстоятельств "
            "(серьёзные травмы, длительные отъезды) — по предварительной договорённости, "
            "не более чем на 7 дней.\n\n"
            "📌 Если тренировка отменяется по инициативе тренера, срок абонемента продлевается.\n"
            "📌 Место в группе закрепляется только при активном абонементе.\n"
            "📌 Оплата следующего абонемента производится до окончания текущего, чтобы сохранить место.\n\n"
            "Проверить абонемент: /check в @acro\\_reminder\\_bot \n\n"
            "ℹ️ Выберите группу ниже, чтобы посмотреть доступные абонементы для регулярных занятий."
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
        "group_4_5": (
            "👧 *Группы 4–5 лет*\n\n"
            "🗓 *Расписание:*\n"
            "Вт / Чт — 17:15–18:15\n"
            "или\n"
            "Вт / Чт — 18:30–19:30\n\n"
            "📌 Смена расписания возможна при наличии свободных мест в группе.\n\n"
            "💳 *Доступные форматы регулярных занятий:*\n"
            "▫️ Абонемент на 5 занятий — 1.250.000₫\n"
            "   _ 250.000₫ за занятие_\n"
            "   Подходит для посещения около 1 раза в неделю.\n\n"
            "▫️ Безлимитный абонемент — 1.600.000₫\n"
            "   _до 10 занятий за абонемент / от 160.000₫ за занятие_\n"
            "   Подходит для регулярного посещения 2 раза в неделю.\n\n"
            "ℹ️ Пробное, разовое и тестовый абонемент на 3 занятия смотрите в разделе *Цены и абонементы*.\n\n"
            "⏳ Абонементы действуют 35 дней и не предусматривают переносов.\n"
            "Подробнее — в разделе *Правила абонементов*."
        ),
        "group_6_9": (
            "🤸 *Группы 6–9 лет*\n\n"
            "🗓 *Расписание:*\n"
            "Начинающие — Пн / Ср / Пт 17:15–18:15\n"
            "Продолжающие — Пн / Ср / Пт 18:30–19:30\n\n"
            "💳 *Доступные форматы регулярных занятий:*\n"
            "▫️ Абонемент на 5 занятий — 1.250.000₫\n"
            "   _250.000₫ за занятие_\n"
            "   Подходит для посещения около 1 раза в неделю.\n\n"
            "▫️ Абонемент на 8 занятий — 1.600.000₫\n"
            "   _200.000₫ за занятие_\n"
            "   Подходит для посещения 2 раза в неделю.\n\n"
            "▫️ Безлимитный абонемент — 2.250.000₫\n"
            "   _до 15 занятий за абонемент / от 150.000₫ за занятие_\n"
            "   Подходит для регулярного посещения и стабильного прогресса.\n\n"
            "ℹ️ Пробное, разовое и тестовый абонемент на 3 занятия смотрите в разделе *Цены и абонементы*.\n\n"
            "⏳ Абонементы действуют 35 дней и не предусматривают переносов.\n"
            "Подробнее — в разделе *Правила абонементов*."
        )
        "group_adults": (
            "🧑 *Взрослая группа*\n\n"
            "Группа для взрослых участников.\n\n"
            "🗓 *Расписание:*\n"
            "Вт / Чт — 10:00–11:00\n\n"
            "💳 *Доступные форматы регулярных занятий:*\n"
            "▫️ Абонемент на 5 занятий — 1.250.000₫\n"
            "   _ 250.000₫ за занятие_\n"
            "   Подходит для посещения около 1 раза в неделю.\n\n"
            "▫️ Безлимитный абонемент — 1.600.000₫\n"
            "   _до 10 занятий за абонемент / от 160.000₫ за занятие_\n"
            "   Подходит для регулярного посещения 2 раза в неделю.\n\n"
            "ℹ️ Пробное, разовое и тестовый абонемент на 3 занятия смотрите в разделе *Цены и абонементы*.\n\n"
            "⏳ Абонементы действуют 35 дней и не предусматривают переносов.\n"
            "Подробнее — в разделе *Правила абонементов*."
        )
    }
    
    text = info_texts.get(section)
    if not text:
        text = "Информация не найдена."
    
# ⬅️ Добавим кнопку «Назад»
    text = info_texts.get(section)
    if not text:
        text = "Информация не найдена."
    
    group_choice_sections = {"prices", "schedule", "abonement"}
    group_sections = {"group_4_5", "group_6_9", "group_adults"}
    
    if section in group_choice_sections:
        reply_markup = get_group_choice_keyboard()
    elif section in group_sections:
        reply_markup = get_group_navigation_keyboard()
    else:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главное меню", callback_data="info|back")]
        ])
    
    await query.edit_message_text(
        text + "\n\n",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
