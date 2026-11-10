"""Read-only Telegram lesson checklist built from existing studio sheets."""
import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from group_config import GROUPS
from sheets_runtime import run_sheets
from subscription_tools import normalize_subscription_type, parse_date
from report_rows import _poll_creation_timestamp
from utils import now_local

SHEET_ID = os.getenv("SPREADSHEET_ID")
SESSION_KEY = "_lesson_list_session"
PAGE_SIZE = 12
MAX_ENTRIES = 75
EXTRA_TRAINER_COST = "−300k₫"


def _is_authorized(user):
    allowed = {
        int(value) for value in (os.getenv("ADMIN_ID"), os.getenv("KARINA_ID"))
        if value and value.lstrip("-").isdigit()
    }
    return user is not None and getattr(user, "id", None) in allowed


def _cell(row, index):
    return str(row[index]).strip() if index is not None and index >= 0 and len(row) > index and row[index] is not None else ""


def _header(row):
    return {str(value).strip().casefold(): index for index, value in enumerate(row) if str(value).strip()}


def _value(row, columns, name):
    return _cell(row, columns.get(name.casefold()))


def _username(value):
    text = str(value or "").strip().lstrip("@").casefold()
    if text in {"", "none", "null", "(без username)"}:
        return ""
    return text


def _tokens(raw, *, usernames=False):
    pieces = str(raw or "").replace(";", ",").split(",")
    return {(_username(piece) if usernames else piece.strip()) for piece in pieces if piece.strip()} - {""}


def _number(raw):
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


def subscription_badge(row, columns, lesson_date):
    """Compact indication only; never treat a guess about Difference as an actual debit."""
    raw = _value(row, columns, "Абонемент")
    kind = normalize_subscription_type(raw)
    if not kind:
        return "— нет абон."
    if kind == "drop_in":
        return "💵 разово"

    end_raw = _value(row, columns, "Срок Действия")
    end_date = parse_date(end_raw, datetime.combine(lesson_date, datetime.min.time())) if end_raw else None
    expired = (end_date is not None and end_date.date() < lesson_date) or (
        not end_date and _value(row, columns, "Days until end").casefold() == "expired"
    )
    warning = _value(row, columns, "warning_7").casefold()
    calendar_left = _number(_value(row, columns, "WO Left until end"))
    difference = _number(_value(row, columns, "Difference"))
    calendar_exhausted = calendar_left == 0 and difference is not None and difference <= 0

    if kind == "unlimited":
        if expired:
            return "⛔ безлимит закончен"
        if warning == "last_calendar_lesson_today":
            return "⚠️ безлимит: последнее занятие"
        if warning == "no_calendar_lessons":
            return "⚠️ безлимит: нет занятий до конца"
        if calendar_exhausted:
            if end_date is not None and end_date.date() == lesson_date:
                return "⚠️ безлимит: последний день"
            return "⚠️ нет занятий после сегодняшнего"
        if calendar_left is not None:
            detail = f"∞ · {calendar_left} зан. до конца"
        else:
            detail = "∞ безлимит"
    else:
        limit = _number(_value(row, columns, "Лимит"))
        if not limit or limit < 1:
            limit = {"sub_8": 8, "sub_5": 5, "sub_3_trial": 3}.get(kind)
        used = _number(_value(row, columns, "Used"))
        left = _number(_value(row, columns, "Unused"))
        if left is None and limit is not None and used is not None:
            left = limit - used
        if expired or (left is not None and left <= 0):
            return "⛔ абон. закончен"
        if warning == "last_calendar_lesson_today":
            return "⚠️ последнее занятие"
        if warning == "no_calendar_lessons":
            return "⚠️ нет занятий до конца"
        if calendar_exhausted:
            if end_date is not None and end_date.date() == lesson_date:
                return "⚠️ последнее занятие"
            return "⚠️ нет занятий после сегодняшнего"
        detail = f"🎟 {used}/{limit}" if used is not None and limit else f"🎟 {raw}"
        if left is not None:
            detail += f" · ост. {left}"
        if left == 1:
            detail += " ⚠️"

    if end_date is not None and end_date.date() == lesson_date:
        detail += " · последний день"
    elif warning in {"last_calendar_lesson", "last_calendar_lesson_today"}:
        detail += " · посл. занятие"
    return detail


def _latest_poll_id(group, today, reports, surveys):
    """Prefer the current lesson, then next scheduled lesson, then previous one."""
    dates = []
    for offset in (0, 1, -1):
        candidate = today + timedelta(days=offset)
        if candidate.strftime("%A") in group.get("days", []):
            dates.append(candidate)
    if not dates:
        dates = [today]

    candidates = {}
    for position, row in enumerate(reports):
        if len(row) < 7 or _cell(row, 1) != group["name"] or not _cell(row, 0):
            continue
        try:
            lesson_date = datetime.strptime(_cell(row, 6)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if lesson_date in dates:
            candidates[lesson_date] = (position, _cell(row, 0))

    # Reports are authoritative; creation rows cover lost/uncatalogued reports.
    for position, row in enumerate(surveys):
        if _cell(row, 1) != group["name"] or not _cell(row, 0):
            continue
        created = _poll_creation_timestamp(row)
        if created is None:
            continue
        lesson_date = created.date() + timedelta(days=group.get("check_day_offset", 0))
        if lesson_date in dates and lesson_date not in candidates:
            candidates[lesson_date] = (position, _cell(row, 0))

    for date in dates:
        if date in candidates:
            return candidates[date][1], date
    return None, dates[0]


def _latest_votes(surveys, poll_id):
    votes = {}
    if not poll_id:
        return []
    for position, row in enumerate(surveys):
        if _cell(row, 0) != poll_id or not _cell(row, 2) or not _cell(row, 6):
            continue
        # The last persisted answer for a given voter wins.
        voter_id = _cell(row, 2)
        votes[voter_id] = {
            "id": voter_id, "username": _username(_cell(row, 3)),
            "name": _cell(row, 5), "answer": _cell(row, 6),
            "position": position,
        }
    return sorted(votes.values(), key=lambda item: item["position"])


def _vote_badge(answer):
    value = str(answer or "").casefold()
    if "пропуска" in value:
        return "❌ не будет"
    if "разово" in value:
        return "💵 будет разово"
    if "по абонементу" in value:
        return "✅ будет"
    return "❔ нет ответа"


def build_lesson_snapshot(group, today, roster_data, report_data, survey_data, usernames_data):
    if not roster_data:
        raise ValueError("Вкладка группы пуста")
    columns = _header(roster_data[0])
    if not {"имя", "группа", "абонемент"}.issubset(columns):
        raise ValueError("Не найдены обязательные колонки Имя, Группа, Абонемент")

    poll_id, lesson_date = _latest_poll_id(group, today, report_data, survey_data)
    votes = _latest_votes(survey_data, poll_id)

    # Username rows are an optional identity fallback, never a sorting source.
    fallback = {}
    if usernames_data:
        ucols = _header(usernames_data[0])
        for row in usernames_data[1:]:
            if _value(row, ucols, "тех группа") != group["name"]:
                continue
            name = _value(row, ucols, "имя").casefold()
            if name:
                ids = set()
                names = set()
                for field in ("User ID", "telegram user id", "user_id"):
                    ids |= _tokens(_value(row, ucols, field))
                for field in ("username1", "username2", "username"):
                    names |= _tokens(_value(row, ucols, field), usernames=True)
                old_ids, old_names = fallback.get(name, (set(), set()))
                fallback[name] = (old_ids | ids, old_names | names)

    students = []
    matched = set()
    for position, row in enumerate(roster_data[1:], start=3):
        name = _value(row, columns, "Имя")
        if not name:
            continue
        if group["key"] != "adult" and _value(row, columns, "Группа").casefold() != group["sheet_group"].casefold():
            continue
        ids = _tokens(_value(row, columns, "User ID"))
        names = _tokens(_value(row, columns, "username"), usernames=True)
        extra_ids, extra_names = fallback.get(name.casefold(), (set(), set()))
        ids |= extra_ids
        names |= extra_names
        applicable = [vote for vote in votes if vote["id"] in ids or (vote["username"] and vote["username"] in names)]
        vote = max(applicable, key=lambda item: item["position"]) if applicable else None
        for item in applicable:
            matched.add(item["id"])
        students.append({
            "name": name,
            "status": subscription_badge(row, columns, lesson_date),
            "vote": _vote_badge(vote["answer"] if vote else ""),
            "new": False, "selected": False, "row": position,
        })

    newcomers = []
    for vote in votes:
        if vote["id"] in matched:
            continue
        identity = f"@{vote['username']}" if vote["username"] else (vote["name"] or f"ID {vote['id']}")
        newcomers.append({
            "name": f"🆕 {identity}",
            "status": "уточнить ученика",
            "vote": _vote_badge(vote["answer"]),
            "new": True, "selected": False,
        })
    if len(students) + len(newcomers) > MAX_ENTRIES:
        raise ValueError("Список слишком большой для одного сообщения")

    return {
        "id": secrets.token_hex(4), "group": group["key"], "title": group["display_name"],
        "date": lesson_date.isoformat(), "poll_found": bool(poll_id),
        "rows": students + newcomers, "extra_trainer": False, "page": 0,
    }


async def load_lesson_snapshot(group):
    ranges = [
        f"'{group['sheet_name']}'!A2:AH",
        "'Репорты'!A2:G",
        "'Опросы'!A2:G",
        "'usernames'!A1:N",
    ]
    result = await run_sheets("lesson checklist read-only snapshot", lambda service: service.values().batchGet(
        spreadsheetId=SHEET_ID, ranges=ranges
    ).execute())
    value_ranges = result.get("valueRanges", [])
    if len(value_ranges) != len(ranges):
        raise ValueError("Не все вкладки были загружены")
    data = [part.get("values", []) for part in value_ranges]
    return build_lesson_snapshot(group, now_local().date(), *data)


def _menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(group["display_name"], callback_data=f"al:g:{group['key']}")]
        for group in GROUPS
    ])


def _render(session):
    rows = session["rows"]
    page = session["page"]
    start = page * PAGE_SIZE
    end = min(len(rows), start + PAGE_SIZE)
    count = sum(bool(row["selected"]) for row in rows)
    header = (
        f"📋 {session['title']}\n"
        f"Занятие: {session['date']}\n"
        f"Опрос: {'найден' if session['poll_found'] else 'нет свежего опроса'}\n"
        f"Отмечено: {count}/{len(rows)}\n\n"
        "Нажмите на ученика, чтобы отметить фактическое присутствие.\n"
        "Значок ответа справа — только информация из опроса."
    )
    keyboard = []
    for index in range(start, end):
        row = rows[index]
        checkbox = "✅" if row["selected"] else "☐"
        vote_icon = row["vote"].split()[0]
        status = row["status"].replace(
            "⚠️ нет занятий после сегодняшнего", "⚠️ после сегодня 0 зан."
        ).replace(
            "⚠️ безлимит: нет занятий до конца", "⚠️ ∞: нет занятий"
        ).replace(
            "⚠️ безлимит: последнее занятие", "⚠️ ∞: посл. занятие"
        )
        prefix = f"{checkbox} {row['name'][:22]} {vote_icon} · "
        label = prefix + status[:max(0, 64 - len(prefix))]
        keyboard.append([InlineKeyboardButton(label, callback_data=f"al:t:{session['id']}:{index}")])
    paging = []
    if start:
        paging.append(InlineKeyboardButton("◀️ Назад", callback_data=f"al:p:{session['id']}:{page-1}"))
    if end < len(rows):
        paging.append(InlineKeyboardButton("Далее ▶️", callback_data=f"al:p:{session['id']}:{page+1}"))
    if paging:
        keyboard.append(paging)
    keyboard.extend([
        [InlineKeyboardButton(
            ("✅" if session["extra_trainer"] else "☐") + f" Доп. тренер ({EXTRA_TRAINER_COST})",
            callback_data=f"al:a:{session['id']}"
        )],
        [InlineKeyboardButton("📋 Получить текст для копирования", callback_data=f"al:o:{session['id']}")],
        [InlineKeyboardButton("🔄 Обновить (сброс отметок)", callback_data=f"al:g:{session['group']}"),
         InlineKeyboardButton("↩️ Другие группы", callback_data=f"al:m:{session['id']}")],
    ])
    return header, InlineKeyboardMarkup(keyboard)


def _export(session):
    rows = [r for r in session["rows"] if r["selected"]]
    if not rows:
        return None
    lines = [f"📋 {session['title']} · {session['date']}"]
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {row['name']}")
    lines.extend([
        "", f"Доп. тренер: {EXTRA_TRAINER_COST if session['extra_trainer'] else 'нет'}",
        "", "Оплаты:", "Новенькие:", "Комментарии:",
    ])
    text = "\n".join(lines)
    if len(text) > 4096:
        raise ValueError("Список слишком длинный для Telegram")
    return text


async def list_command(update, context):
    if not _is_authorized(update.effective_user):
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    if getattr(update.effective_chat, "type", None) != "private":
        await update.message.reply_text("Откройте личный чат с ботом и отправьте /list.")
        return
    context.user_data.pop(SESSION_KEY, None)
    await update.message.reply_text("📋 Выберите группу для списка занятия:", reply_markup=_menu())


async def list_callback(update, context):
    query = update.callback_query
    if not _is_authorized(update.effective_user) or getattr(update.effective_chat, "type", None) != "private":
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    parts = str(query.data or "").split(":")
    if len(parts) < 3 or parts[0] != "al":
        await query.answer("Неизвестное действие", show_alert=True)
        return
    await query.answer()

    if parts[1] == "g":
        group = next((item for item in GROUPS if item["key"] == parts[2]), None)
        if not group:
            await query.edit_message_text("Неизвестная группа.")
            return
        await query.edit_message_text("⏳ Загружаю список и ответы опроса…")
        try:
            sheet_group = {**group, "sheet_name": (
                "Группы 4-5" if group["key"].startswith("junior_")
                else "Группы 6-9" if group["key"].startswith("69_")
                else "Взрослая группа"
            )}
            session = await load_lesson_snapshot(sheet_group)
        except Exception:
            logging.exception("Failed to load lesson checklist for group=%s", group["key"])
            await query.edit_message_text("⚠️ Не удалось загрузить список. Попробуйте снова.", reply_markup=_menu())
            return
        context.user_data[SESSION_KEY] = session
        text, keyboard = _render(session)
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    session = context.user_data.get(SESSION_KEY)
    if not session or parts[2] != session["id"]:
        await query.edit_message_text("Список устарел. Отправьте /list заново.")
        return
    action = parts[1]
    if action == "t" and len(parts) == 4:
        index = _number(parts[3])
        if index is None or index < 0 or index >= len(session["rows"]):
            return
        row = session["rows"][index]
        row["selected"] = not row["selected"]
    elif action == "p" and len(parts) == 4:
        page = _number(parts[3])
        if page is None or page < 0 or page * PAGE_SIZE >= max(1, len(session["rows"])):
            return
        session["page"] = page
    elif action == "a":
        session["extra_trainer"] = not session["extra_trainer"]
    elif action == "m":
        context.user_data.pop(SESSION_KEY, None)
        await query.edit_message_text("📋 Выберите группу для списка занятия:", reply_markup=_menu())
        return
    elif action == "o":
        text = _export(session)
        if text is None:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Сначала отметьте присутствующих учеников.")
            return
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        return
    else:
        return
    text, keyboard = _render(session)
    await query.edit_message_text(text, reply_markup=keyboard)
