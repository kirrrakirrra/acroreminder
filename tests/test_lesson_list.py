import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import lesson_list_handler as lists


COLUMNS = [
    "Имя", "Группа", "User ID", "username", "Абонемент", "Лимит",
    "Used", "Unused", "Срок Действия", "WO Left until end",
    "Difference", "warning_7",
]


def student(name, group="6-9 лет начинающие", user_id="", username="", sub="абон 5",
            limit="5", used="2", unused="3", end="20.11.2026",
            calendar="", difference="", warning=""):
    return [name, group, user_id, username, sub, limit, used, unused,
            end, calendar, difference, warning]


def poll_vote(user_id, answer, username="", full_name="Родитель"):
    return ["poll-1", "Старшей начинающей группы", str(user_id),
            "@" + username if username else "", "2026-11-09 10:00",
            full_name, answer]


def sample_snapshot():
    group = dict(lists.GROUPS[2])
    roster = [
        COLUMNS,
        student("Дима", user_id="11", used="5", unused="0"),
        student("Аня", username="", sub="безлимит", used="8", unused="",
                calendar="2", difference="2"),
        student("Лиза", sub="Разово", used="", unused=""),
        student("Другая группа", group="6-9 лет продолжающие", user_id="99"),
    ]
    reports = [["poll-1", group["name"], "", "", "", "", "2026-11-09"]]
    surveys = [
        poll_vote(11, "✅ Будем по абонементу"),
        poll_vote(22, "✅ Будем по абонементу", username="mother"),
        poll_vote(11, "❌ Пропускаем"),
        poll_vote(33, "💵 Будем разово", username="new_parent"),
        ["poll-OLD", group["name"], "11", "", "", "Родитель", "✅ Будем по абонементу"],
    ]
    usernames = [
        ["имя", "username1", "тех группа"],
        ["Аня", "mother", group["name"]],
    ]
    return lists.build_lesson_snapshot(
        group, date(2026, 11, 9), roster, reports, surveys, usernames,
    )


def test_group_sheet_order_last_vote_and_unknown_respondent():
    state = sample_snapshot()
    assert [row["name"] for row in state["rows"]] == [
        "Дима", "Аня", "Лиза", "🆕 @new_parent",
    ]
    assert [row["vote"] for row in state["rows"]] == [
        "❌ не будет", "✅ будет", "❔ нет ответа", "💵 будет разово",
    ]
    assert "закончен" in state["rows"][0]["status"]
    assert "∞ · 2 зан." in state["rows"][1]["status"]
    assert "разово" in state["rows"][2]["status"]
    assert all(not row["selected"] for row in state["rows"])


def test_expiry_on_last_day_is_still_valid_and_difference_alone_does_not_expire():
    c = lists._header(COLUMNS)
    last_day = student("Аня", used="4", unused="1", end="09.11.2026")
    assert "закончен" not in lists.subscription_badge(last_day, c, date(2026, 11, 9))
    assert "последний день" in lists.subscription_badge(last_day, c, date(2026, 11, 9))
    assert "закончен" in lists.subscription_badge(last_day, c, date(2026, 11, 10))
    unlimited = student("Лена", sub="безлимит", used="7", unused="",
                        calendar="3", difference="0")
    assert "закончен" not in lists.subscription_badge(unlimited, c, date(2026, 11, 9))
    exhausted_calendar = student("Лена", sub="безлимит", used="8", unused="",
                                 calendar="0", difference="0")
    assert "нет занятий" in lists.subscription_badge(exhausted_calendar, c, date(2026, 11, 9))
    end_today = student("Лена", sub="безлимит", used="8", unused="",
                        end="09.11.2026", calendar="0", difference="0")
    assert "последний день" in lists.subscription_badge(end_today, c, date(2026, 11, 9))
    limited_no_calendar = student("Оля", used="2", unused="3", calendar="0", difference="0")
    assert "нет занятий" in lists.subscription_badge(limited_no_calendar, c, date(2026, 11, 9))
    limited_last_day = student("Оля", used="2", unused="3", end="09.11.2026",
                               calendar="0", difference="0")
    assert "последнее занятие" in lists.subscription_badge(limited_last_day, c, date(2026, 11, 9))


def test_old_poll_not_reused_for_new_lesson():
    group = dict(lists.GROUPS[2])
    reports = [["poll-1", group["name"], "", "", "", "", "2026-10-01"]]
    survey = [poll_vote(11, "✅ Будем по абонементу")]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 9), [COLUMNS, student("Дима", user_id="11")],
        reports, survey, [],
    )
    assert not state["poll_found"]
    assert state["rows"][0]["vote"] == "❔ нет ответа"


def test_export_preserves_sheet_order_even_if_toggled_out_of_order():
    session = sample_snapshot()
    session["rows"][2]["selected"] = True
    session["rows"][0]["selected"] = True
    session["rows"][3]["selected"] = True
    session["extra_trainer"] = True
    export = lists._export(session)
    assert export.index("1. Дима") < export.index("2. Лиза") < export.index("3. 🆕 @new_parent")
    assert "Доп. тренер: −300k₫" in export
    assert "Оплаты:" in export and "Комментарии:" in export


def test_no_selected_students_has_no_export():
    assert lists._export(sample_snapshot()) is None


def test_admin_only_private_command(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("KARINA_ID", "2")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99),
        effective_chat=SimpleNamespace(type="private"),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(user_data={})
    asyncio.run(lists.list_command(update, context))
    assert "только администратору" in update.message.reply_text.await_args.args[0]

    update.effective_user.id = 1
    update.effective_chat.type = "supergroup"
    asyncio.run(lists.list_command(update, context))
    assert "личный чат" in update.message.reply_text.await_args.args[0]


def test_callback_checklist_and_copy(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    state = sample_snapshot()

    async def fake_loader(_group):
        return state

    monkeypatch.setattr(lists, "load_lesson_snapshot", fake_loader)
    query = SimpleNamespace(
        data="al:g:69_beginner", answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        callback_query=query,
    )
    context = SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    async def scenario():
        await lists.list_callback(update, context)
        assert context.user_data[lists.SESSION_KEY] is state
        assert query.edit_message_text.await_count == 2

        query.data = f"al:t:{state['id']}:2"
        await lists.list_callback(update, context)
        assert state["rows"][2]["selected"] is True

        query.data = f"al:a:{state['id']}"
        await lists.list_callback(update, context)
        assert state["extra_trainer"]

        query.data = f"al:o:{state['id']}"
        await lists.list_callback(update, context)
        output = context.bot.send_message.await_args.kwargs["text"]
        assert "1. Лиза" in output and "−300k₫" in output

        query.data = "al:t:obsolete:1"
        await lists.list_callback(update, context)
        assert "устарел" in query.edit_message_text.await_args.args[0]

    asyncio.run(scenario())


def test_list_handlers_are_registered_and_command_deduplicated():
    from pathlib import Path
    source = Path("main.py").read_text()
    assert 'CommandHandler("list", list_command)' in source
    assert 'CallbackQueryHandler(list_callback, pattern=r"^al:")' in source
    assert '"/list"' in source
