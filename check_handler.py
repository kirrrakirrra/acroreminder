from telegram import Update
from telegram.ext import ContextTypes
import os
import asyncio
import logging
import datetime
import time
from utils import notify_karina_action
from group_config import GROUP_NAME_MAP, GROUPS
from scheduler_handler import check_expired_subscriptions, groups
from subscription_tools import (
    load_all_subscriptions,
    find_user_subscriptions,
    format_usage,
    is_finished,
    get_subscription_alert_status,
    SUBSCRIPTION_SHEETS,
)

# -----------------------------
# Вспомогательные функции
# -----------------------------

def has_7_days_warning(subscription: dict) -> bool:
    value = str(subscription.get("warning_7", "")).strip().lower()
    return value in {
        "warning_7",
        "no_calendar_lessons",
        "last_calendar_lesson_today",
        "last_calendar_lesson",
    }


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

    status = get_subscription_alert_status(subscription)
    if status in {
        "expired",
        "finished",
        "last_lesson",
        "no_calendar_lessons",
        "last_calendar_lesson_today",
        "last_calendar_lesson",
    }:
        return ""

    difference_value = str(subscription.get("difference", "")).strip()
    if not difference_value:
        return ""

    unused = subscription.get("unused", 0)
    wo_left = subscription.get("wo_left_until_end", 0)

    return (
        f"\n\n⚠️ Неиспользованные занятия: *{unused}*\n"
        f"Тренировок до конца абонемента: *{wo_left}*\n\n"
        "Неиспользованные занятия не переносятся."
    )


def get_unlimited_info(subscription: dict) -> str:
    if subscription.get("subscription_type") != "unlimited":
        return ""

    status = get_subscription_alert_status(subscription)

    # Если абонемент уже истёк, не показываем "осталось дней: expired"
    if status == "expired":
        return ""

    # На всякий случай, если статус когда-то расширится
    if status == "finished":
        return ""

    # если уже есть warning_7 — обычный блок не показываем
    if has_7_days_warning(subscription):
        return ""

    days_until_end = str(subscription.get("days_until_end", "")).strip()

    if not days_until_end:
        return ""

    # дополнительная защита от технических значений из таблицы
    if days_until_end.lower() == "expired":
        return ""

    return (
        f"\n⏳ *До конца абонемента осталось дней:* `{days_until_end}`"
    )
    
def get_payment_reminder_text(subscription: dict) -> str:
    status = get_subscription_alert_status(subscription)

    if status == "expired":
        return (
            "\n\n🔚 *Срок действия абонемента истёк.*\n"
            "Не забудьте оплатить следующий абонемент, "
            "чтобы сохранить место в группе."
        )

    if status == "finished":
        return (
            "\n\n🔚 *Абонемент завершён.*\n"
            "Не забудьте оплатить следующий абонемент, "
            "чтобы сохранить место в группе."
        )

    if status == "last_lesson":
        return (
            "\n\n📌 *Осталось последнее занятие*\n"
            "Пожалуйста, внесите оплату за следующий абонемент, "
            "чтобы сохранить место в группе."
        )

    return ""

def get_warning_7_text(subscription: dict) -> str:
    status = get_subscription_alert_status(subscription)

    if status == "no_calendar_lessons":
        return (
            "\n\n⛔️ *Абонемент завершён по расписанию*\n"
            "По расписанию больше нет занятий, которые попадают в срок этого абонемента.\n\n"
            "Пожалуйста, внесите оплату за следующий абонемент, "
            "чтобы сохранить место в группе."
        )

    if status == "last_calendar_lesson_today":
        return (
            "\n\n🚨 *Сегодня последнее занятие*, "
            "которое попадает в срок действия этого абонемента.\n"
            "Пожалуйста, внесите оплату за следующий абонемент."
        )

    if status == "last_calendar_lesson":
        return (
            "\n\n❕🗓️ *Осталось одно занятие в рамках абонемента*\n"
            "По расписанию в этот абонемент входит только ещё одно занятие.\n\n"
            "Пожалуйста, внесите оплату за следующий абонемент."
        )

    if status != "warning_7":
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
    status = get_subscription_alert_status(subscription)
    logging.info(
        f"[check-debug] name={subscription.get('name')}, "
        f"end_date_raw={subscription.get('end_date_raw')}, "
        f"end_date={subscription.get('end_date')}, "
        f"unused={subscription.get('unused')}, "
        f"status={status}"
    )

    msg = (
        f"👤 *Имя:* `{name}`\n"
        f"🏷️ *Группа:* `{group}`\n"
        f"🧾 *Абонемент:* `{sub_type}`\n"
        f"📆 *Срок действия:* `{start} — {end}`\n"
        f"✅ *Использовано:* `{usage_text}`\n"
        f"📅 *Даты посещений:*\n{dates_text}"
        f"{limited_warning}"
        f"{unlimited_info}"
        f"{warning_7_text}"
        f"{payment_reminder_text}"
    )

    return msg

# -----------------------------
# /check
# -----------------------------

GROUP_SHEET_BY_KEY = {
    "junior_1715": "Группы 4-5",
    "junior_1830": "Группы 4-5",
    "69_beginner": "Группы 6-9",
    "69_pro": "Группы 6-9",
    "adult": "Взрослая группа",
}


def get_subscription_sheets_for_chat(chat) -> list[str]:
    """Restrict known group chats; private and unknown chats search every sheet."""
    if not chat or getattr(chat, "type", None) not in {"group", "supergroup"}:
        return list(SUBSCRIPTION_SHEETS)

    chat_id = str(chat.id)
    for group in GROUPS:
        if group.get("group_id") and str(group["group_id"]) == chat_id:
            return [GROUP_SHEET_BY_KEY[group["key"]]]
    return list(SUBSCRIPTION_SHEETS)


async def check_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_username = update.effective_user.username
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name

    logging.info(f"/check used by {full_name} (@{raw_username}) [ID: {user_id}]")

    user = update.effective_user
    chat = update.effective_chat
    effective_message = update.effective_message
    chat_id = chat.id
    thread_id = getattr(effective_message, "message_thread_id", None)
    sheet_names = get_subscription_sheets_for_chat(chat)
    lookup_scope = sheet_names[0] if len(sheet_names) == 1 else "all"
    logging.info(
        "/check lookup chat_id=%s message_thread_id=%s sheets=%s",
        chat_id, thread_id, lookup_scope,
    )

    async def send_user_message(text: str, **kwargs) -> bool:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                **kwargs,
            )
            logging.info(
                "/check user send succeeded chat_id=%s message_thread_id=%s",
                chat_id, thread_id,
            )
            return True
        except Exception:
            logging.exception(
                "/check user send failed chat_id=%s message_thread_id=%s",
                chat_id, thread_id,
            )
            return False

    async def notify_after_send(sent: bool) -> None:
        if sent:
            await notify_karina_action(context, user, "🔍 /check")

    load_started = time.monotonic()
    try:
        all_subscriptions = await asyncio.to_thread(load_all_subscriptions, sheet_names)
    except Exception as e:
        logging.warning(f"❗ Ошибка при загрузке абонементов: {e}")
        sent = await send_user_message("❌ Не удалось прочитать данные абонементов из таблицы.")
        await notify_after_send(sent)
        return
    finally:
        logging.info(
            "/check Sheets load chat_id=%s message_thread_id=%s sheets=%s duration=%.3fs",
            chat_id, thread_id, lookup_scope, time.monotonic() - load_started,
        )

    user_subscriptions = find_user_subscriptions(
        all_subscriptions=all_subscriptions,
        telegram_user_id=user_id,
        telegram_username=raw_username,
    )

    # Убираем разовые — для /check они не считаются абонементами
    user_subscriptions = [
        sub for sub in user_subscriptions
        if sub.get("subscription_type") not in {"", None, "drop_in"}
    ]
    logging.info(
        "/check matches chat_id=%s message_thread_id=%s count=%d",
        chat_id, thread_id, len(user_subscriptions),
    )

    if not user_subscriptions:
        sent = await send_user_message(
            "⚠️ У вас нет активных абонементов, или ваш username / user ID не добавлен в таблицу, пожалуйста, обратитесь к администратору.\n\n"
            "ℹ️ Чтобы узнать *информацию* о расписании, ценах и правилах — воспользуйтесь командой /info.",
            parse_mode="Markdown"
        )
        await notify_after_send(sent)
        return

    messages = [build_subscription_message(sub) for sub in user_subscriptions]

    all_sent = True
    for msg in messages:
        all_sent = await send_user_message(msg, parse_mode="Markdown") and all_sent
    await notify_after_send(all_sent)


# -----------------------------
# /expired — пока оставляем как есть
# -----------------------------

async def expired_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    await notify_karina_action(context, user, "⚠️ /expired")
    full_name = update.effective_user.full_name
    username = update.effective_user.username or ""
    logging.info(f"/expired used by {full_name} (@{username}) [ID: {user_id}]")
    admin_id = os.getenv("ADMIN_ID")

    if str(user_id) != str(admin_id):
        await update.message.reply_text(
            "⛔ Эта команда доступна только администратору.\n"
            "Для проверки абонемента воспользуйтесь командой /check."
        )
        return

    now = datetime.datetime.utcnow() + datetime.timedelta(hours=7)
    weekday = now.strftime("%A")

    today_groups = [
        GROUP_NAME_MAP.get(group["name"])
        for group in groups
        if weekday in group["days"]
    ]
    today_groups = [g for g in today_groups if g]

    await check_expired_subscriptions(context.application, today_groups)
    await update.message.reply_text("✅ Проверка завершённых абонементов выполнена.")
