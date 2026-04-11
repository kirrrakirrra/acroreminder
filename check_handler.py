from telegram import Update
from telegram.ext import ContextTypes
import os
import logging
import datetime

from scheduler_handler import check_expired_subscriptions, groups
from subscription_tools import (
    load_all_subscriptions,
    find_user_subscriptions,
    format_usage,
    is_finished,
)

# -----------------------------
# Вспомогательные функции
# -----------------------------

def has_7_days_warning(subscription: dict) -> bool:
    value = str(subscription.get("warning_7", "")).strip().lower()
    return value == "warning_7"


def build_visit_dates_text(subscription: dict) -> str:
    """
    Для лимитных абонементов:
    показываем ровно столько строк, сколько лимит.
    Для безлимита:
    показываем только реально использованные даты.
    """
    visit_dates = subscription.get("visit_dates", [])
    subscription_type = subscription.get("subscription_type")
    limit_value = subscription.get("limit", 0)

    # Безлимит — только реальные даты
    if subscription_type == "unlimited":
        if not visit_dates:
            return "—"
        return "\n".join(
            [f"{i}. {date}" for i, date in enumerate(visit_dates, start=1)]
        )

    # Лимитные абонементы — ровно limit строк
    try:
        limit_value = int(limit_value)
    except Exception:
        limit_value = 0

    if limit_value <= 0:
        if not visit_dates:
            return "—"
        return "\n".join(
            [f"{i}. {date}" for i, date in enumerate(visit_dates, start=1)]
        )

    lines = []
    for i in range(1, limit_value + 1):
        value = visit_dates[i - 1] if i - 1 < len(visit_dates) else "—"
        lines.append(f"{i}. {value}")

    return "\n".join(lines)


def get_limited_subscription_warning(subscription: dict) -> str:
    """
    Для лимитных абонементов:
    если в колонке Difference что-то есть, показываем предупреждение.
    """
    if subscription.get("subscription_type") == "unlimited":
        return ""

    difference_value = str(subscription.get("difference", "")).strip()
    if not difference_value:
        return ""

    unused = subscription.get("unused", 0)
    wo_left = subscription.get("wo_left_until_end", 0)

    return (
        "\n\n⚠️ Неиспользованные занятия: *{unused}*\n"
        f"Тренировок до конца абонемента: *{wo_left}*\n\n"
        "Неиспользованные занятия не переносятся."
    )


def get_unlimited_info(subscription: dict) -> str:
    """
    Для безлимита:
    показываем дату конца и количество оставшихся дней.
    """
    if subscription.get("subscription_type") != "unlimited":
        return ""

    end_date = subscription.get("end_date_raw", "—")
    days_until_end = str(subscription.get("days_until_end", "")).strip()

    if not days_until_end:
        return ""

    return (
        f"\n\n📌 *До конца абонемента:* `{end_date}`\n"
        f"⏳ *Осталось дней:* `{days_until_end}`"
    )

def get_payment_reminder_text(subscription: dict) -> str:
    if subscription.get("subscription_type") == "unlimited":
        return ""

    unused_raw = str(subscription.get("unused", "")).strip()
    if unused_raw == "":
        return ""

    try:
        unused = int(unused_raw)
    except ValueError:
        return ""

    if unused == 1:
        return (
            "\n\n📌 *Осталось последнее занятие*\n"
            "Пожалуйста, внесите оплату за следующий абонемент, "
            "чтобы сохранить место в группе."
        )

    if unused == 0:
        return (
            "\n\n🔚 *Абонемент завершён.*\n"
            "Не забудьте оплатить следующий абонемент, "
            "чтобы сохранить место в группе."
        )

    return ""

def get_warning_7_text(subscription: dict) -> str:
    if not has_7_days_warning(subscription):
        return ""

    # 👉 проверяем Unused
    unused_raw = str(subscription.get("unused", "")).strip()

    try:
        unused = int(unused_raw)
    except ValueError:
        unused = None

    # 👉 если осталось 1 или 0 — НЕ показываем warning_7
    if unused in (0, 1):
        return ""

    end_date = subscription.get("end_date_raw", "—")

    return (
        "\n\n⏳ *До конца абонемента осталось менее 7 дней.*\n"
        f"Пожалуйста, внесите оплату за следующий абонемент до *{end_date}*, "
        "чтобы сохранить место в группе."
    )


def build_subscription_message(subscription: dict) -> str:
    name = subscription.get("name", "—")
    group = subscription.get("group", "—")
    sub_type = subscription.get("subscription_type_raw", "—")
    start = subscription.get("start_date_raw", "—")
    end = subscription.get("end_date_raw", "—")
    usage_text = format_usage(subscription)
    dates_text = build_visit_dates_text(subscription)

    limited_warning = get_limited_subscription_warning(subscription)
    unlimited_info = get_unlimited_info(subscription)
    warning_7_text = get_warning_7_text(subscription)
    payment_reminder_text = get_payment_reminder_text(subscription)

    msg = (
        f"👤 *Имя:* `{name}`\n"
        f"🏷️ *Группа:* `{group}`\n"
        f"🧾 *Абонемент:* `{sub_type}`\n"
        f"📆 *Срок действия:* `{start} — {end}`\n"
        f"✅ *Использовано:* `{usage_text}`\n"
        f"📅 *Даты посещений:*\n{dates_text}"
        f"{finished_text}"
        f"{limited_warning}"
        f"{unlimited_info}"
        f"{warning_7_text}"
        f"{payment_reminder_text}"
    )

    return msg

# -----------------------------
# /check
# -----------------------------

async def check_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_username = update.effective_user.username
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name

    logging.info(f"/check used by {full_name} (@{raw_username}) [ID: {user_id}]")

    karina_id = os.getenv("KARINA_ID")
    if karina_id:
        try:
            await context.bot.send_message(
                chat_id=karina_id,
                text=f"👀 Команду /check использовал: {full_name} (@{raw_username}) [ID: {user_id}]"
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение админу: {e}")

    try:
        all_subscriptions = load_all_subscriptions()
    except Exception as e:
        logging.warning(f"❗ Ошибка при загрузке абонементов: {e}")
        return await update.message.reply_text("❌ Не удалось прочитать данные абонементов из таблицы.")

    if not all_subscriptions:
        return await update.message.reply_text("Таблица пуста или недоступна.")

    user_subscriptions = find_user_subscriptions(
        all_subscriptions=all_subscriptions,
        telegram_user_id=user_id,
        telegram_username=raw_username,
    )

    if not user_subscriptions:
        return await update.message.reply_text(
            "⚠️ У вас нет активных абонементов, или ваш username / user ID не добавлен в таблицу, пожалуйста, обратитесь к администратору.\n\n"
            "ℹ️ Чтобы узнать *информацию* о расписании, ценах и правилах — воспользуйтесь командой /info.",
            parse_mode="Markdown"
        )

    messages = [build_subscription_message(sub) for sub in user_subscriptions]

    for msg in messages:
        await update.message.reply_text(msg, parse_mode="Markdown")


# -----------------------------
# /expired — пока оставляем как есть
# -----------------------------

async def expired_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin_id = os.getenv("ADMIN_ID")

    if str(user_id) != str(admin_id):
        await update.message.reply_text("⛔ Команда доступна только администратору.")
        return

    now = datetime.datetime.utcnow() + datetime.timedelta(hours=7)
    weekday = now.strftime("%A")

    group_name_map = {
        "Старшей начинающей группы": "6-9 лет начинающие",
        "Старшей продолжающей группы": "6-9 лет продолжающие",
        "Младшей группы": "4-5 лет",
    }

    today_groups = [
        group_name_map.get(group["name"])
        for group in groups
        if weekday in group["days"]
    ]

    await check_expired_subscriptions(context.application, today_groups)
    await update.message.reply_text("✅ Проверка завершённых абонементов выполнена.")
