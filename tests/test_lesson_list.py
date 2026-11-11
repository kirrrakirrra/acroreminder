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
    export = lists._export(session)
    assert export.index("1. Дима") < export.index("2. Лиза") < export.index("3. 🆕 @new_parent")
    assert "Доп. тренер" not in export
    assert "Оплаты:" not in export
    assert "Новенькие:" not in export
    assert "Комментарии:" not in export


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

        _, keyboard = lists._render(state)
        assert not any(
            "Доп. тренер" in button.text
            for row in keyboard.inline_keyboard for button in row
        )

        query.data = f"al:o:{state['id']}"
        await lists.list_callback(update, context)
        output = context.bot.send_message.await_args.kwargs["text"]
        assert "1. Лиза" in output
        assert not any(label in output for label in
                       ("−300k₫", "Доп. тренер", "Оплаты:", "Новенькие:", "Комментарии:"))

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


def test_today_poll_wins_over_tomorrow_and_older_survey():
    group = {**lists.GROUPS[2], "days": ["Monday", "Tuesday"]}
    roster = [COLUMNS, student("Дима", user_id="11")]
    reports = [
        ["poll-today", group["name"], "", "", "", "", "2026-11-09"],
        ["poll-tomorrow", group["name"], "", "", "", "", "2026-11-10"],
    ]
    surveys = [
        ["poll-today", group["name"], "11", "", "", "Parent", "❌ Пропускаем"],
        ["poll-tomorrow", group["name"], "11", "", "", "Parent", "✅ Будем по абонементу"],
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 9), roster, reports, surveys, [],
    )
    assert state["date"] == "2026-11-09"
    assert state["rows"][0]["vote"] == "❌ не будет"


def test_adult_day_before_uses_next_day_poll():
    group = dict(lists.GROUPS[-1])
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 9),
        [COLUMNS, student("Таня", group="Взрослая группа", user_id="9")],
        [["poll-tue", group["name"], "", "", "", "", "2026-11-10"]],
        [["poll-tue", group["name"], "9", "", "", "Parent", "✅ Будем по абонементу"]],
        [],
    )
    assert state["date"] == "2026-11-10"
    assert state["rows"][0]["vote"] == "✅ будет"


def test_poll_creation_utc_timestamp_is_converted_to_vietnam_date():
    group = dict(lists.GROUPS[2])
    # Sunday 20:30 UTC is Monday 03:30 in Vietnam.
    survey_metadata = [
        "poll-utc", group["name"], "", "",
        "2026-11-08T20:30:00+00:00", "", "yes|no",
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 9),
        [COLUMNS, student("Дима", user_id="11")], [],
        [survey_metadata, ["poll-utc", group["name"], "11", "", "", "Parent", "❌ Пропускаем"]],
        [],
    )
    assert state["poll_found"] is True
    assert state["date"] == "2026-11-09"
    assert state["rows"][0]["vote"] == "❌ не будет"


def test_last_calendar_lesson_today_never_shows_expired():
    c = lists._header(COLUMNS)
    unlimited = student("Лена", sub="безлимит", used="8", unused="",
                        end="20.11.2026", calendar="0", difference="0",
                        warning="last_calendar_lesson_today")
    status = lists.subscription_badge(unlimited, c, date(2026, 11, 9))
    assert "последнее занятие" in status
    assert "закончен" not in status


def test_button_labels_show_vote_and_subscription_within_phone_friendly_limit():
    state = sample_snapshot()
    _, markup = lists._render(state)
    labels = [row[0].text for row in markup.inline_keyboard[:len(state["rows"])]]
    assert all(len(label) <= 64 for label in labels)
    assert "Дима" in labels[0] and "закончен" in labels[0] and "❌" in labels[0]
    assert "Аня" in labels[1] and "∞" in labels[1] and "✅" in labels[1]
    assert "Лиза" in labels[2] and "разово" in labels[2]


def test_real_usernames_id_columns_and_adult_repeated_section_headers():
    group = dict(lists.GROUPS[-1])
    roster = [
        COLUMNS,
        student("Света", group="Взрослая группа", username=""),
        student("Имя", group="Группа", sub="Абонемент"),
        student("Света", group="Взрослая группа", username="", sub=""),
    ]
    reports = [["poll-adult", group["name"], "", "", "", "", "2026-11-10"]]
    surveys = [["poll-adult", group["name"], "777", "", "",
                "Parent", "✅ Будем по абонементу"]]
    usernames = [
        ["Отчет", "Группа", "имя", "username1", "username2", "Weekdays",
         "user ID1", "user ID2", "Имя Родителя1", "Имя Родителя2",
         "Пауза", "Проголосовали сегодня", "тех группа", "Депозит"],
        ["", "", "Света", "", "", "", "", "777", "", "", "", "",
         group["name"], ""],
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 10), roster, reports, surveys, usernames,
    )
    assert [row["name"] for row in state["rows"]] == ["Света"]
    assert state["rows"][0]["vote"] == "✅ будет"


def test_unknown_subscription_cell_content_is_not_presented_as_real_pass():
    columns = lists._header(COLUMNS)
    note_row = student("Соня", sub="18.06 не будет 10 дней")
    assert lists.subscription_badge(note_row, columns, date(2026, 11, 9)) == "ℹ️ уточнить абон."


def test_more_than_twelve_students_can_be_paginated_without_changing_export_order():
    group = dict(lists.GROUPS[2])
    roster = [COLUMNS] + [
        student(f"Ученик {i:02d}", user_id=str(100 + i))
        for i in range(1, 19)
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 9), roster, [], [], [],
    )
    assert len(state["rows"]) == 18
    _, first_markup = lists._render(state)
    assert any("Далее" in button.text for row in first_markup.inline_keyboard for button in row)
    state["page"] = 1
    _, second_markup = lists._render(state)
    assert "Ученик 13" in second_markup.inline_keyboard[0][0].text
    assert any("Назад" in button.text for row in second_markup.inline_keyboard for button in row)
    state["rows"][13]["selected"] = True
    state["rows"][1]["selected"] = True
    exported = lists._export(state)
    assert exported.index("1. Ученик 02") < exported.index("2. Ученик 14")


def test_duplicate_reference_section_does_not_generate_extra_checkboxes():
    # Actual group tabs have a second, auxiliary table after an "Имя / Группа"
    # heading. Values there are notes, not subscriptions.
    group = dict(lists.GROUPS[3])  # continuing 6–9
    roster = [
        COLUMNS,
        student("Жаркова Майя", group=group["sheet_group"],
                sub="Абон 8", limit="8", used="4", unused="4"),
        student("Заливохина Дарина", group=group["sheet_group"],
                sub="Абон 8", limit="8", used="4", unused="4"),
        student("Стрищенко Глеб", group=group["sheet_group"],
                sub="Безлимит", calendar="11", used="3"),
        student("Имя Ребенка", group="Группа", sub="Коммент"),
        student("Жаркова Майя", group=group["sheet_group"], sub=""),
        student("Заливохина Дарина", group=group["sheet_group"], sub=""),
        student("Стрищенко Глеб", group=group["sheet_group"], sub=""),
        student("Чужая строка из нижней таблицы",
                group=group["sheet_group"], sub=""),
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 11), roster, [], [], [],
    )
    assert [item["name"] for item in state["rows"]] == [
        "Жаркова Майя", "Заливохина Дарина", "Стрищенко Глеб",
    ]
    assert "4/8" in state["rows"][0]["status"]
    assert "4/8" in state["rows"][1]["status"]
    assert "∞" in state["rows"][2]["status"]
    assert "нет абон." not in str([item["status"] for item in state["rows"]])
    _, markup = lists._render(state)
    assert not any("Далее" in button.text
                   for row in markup.inline_keyboard for button in row)
    state["rows"][0]["selected"] = True
    text = lists._export(state)
    assert text.count("Жаркова Майя") == 1
    assert "Комментарии:" not in text and "Доп. тренер" not in text


def test_all_group_tabs_stop_at_secondary_reference_header():
    for group in lists.GROUPS:
        name = "Проверочный ученик"
        roster = [
            COLUMNS,
            student(name, group=group["sheet_group"],
                    sub="Абон 8", limit="8", used="3", unused="5"),
            student("Имя", group="Группа", sub="Коммент"),
            student(name, group=group["sheet_group"], sub=""),
        ]
        state = lists.build_lesson_snapshot(
            group, date(2026, 11, 11), roster, [], [], [],
        )
        assert [entry["name"] for entry in state["rows"]] == [name]
        assert "3/8" in state["rows"][0]["status"]


def test_duplicate_within_primary_roster_keeps_first_status_and_position():
    group = dict(lists.GROUPS[-1])
    roster = [
        COLUMNS,
        student("Анна Стрищенко", group=group["sheet_group"],
                sub="Безлимит", calendar="5"),
        student("Борис", group=group["sheet_group"], sub="Разово"),
        student(" Анна  Стрищенко ", group=group["sheet_group"],
                sub="Безлимит", calendar="0", difference="0"),
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 10), roster, [], [], [],
    )
    assert [entry["name"] for entry in state["rows"]] == [
        "Анна Стрищенко", "Борис",
    ]
    assert "5 зан." in state["rows"][0]["status"]


def test_eleven_primary_and_eleven_secondary_rows_do_not_create_next_page():
    group = dict(lists.GROUPS[3])
    first = [
        student(f"Продолжающий {index}", group=group["sheet_group"],
                sub="Абон 8", limit="8", used="4", unused="4")
        for index in range(11)
    ]
    secondary = [
        student(f"Продолжающий {index}", group=group["sheet_group"], sub="")
        for index in range(11)
    ]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 11),
        [COLUMNS, *first, student("Имя Ребенка", group="Группа"), *secondary],
        [], [], [],
    )
    assert len(state["rows"]) == 11
    assert all("4/8" in item["status"] for item in state["rows"])
    _, keyboard = lists._render(state)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert not any("Далее" in label for label in labels)
