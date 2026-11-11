import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import lesson_list_handler as lists


COLUMNS = [
    "Имя", "Группа", "User ID", "username", "Абонемент", "Лимит",
    "Used", "Unused", "Срок Действия", "WO Left until end",
    "Difference", "warning_7", "Депозит",
]


def student(name, group="6-9 лет начинающие", user_id="", username="", sub="абон 5",
            limit="5", used="2", unused="3", end="20.11.2026",
            calendar="", difference="", warning="", deposit=""):
    return [name, group, user_id, username, sub, limit, used, unused,
            end, calendar, difference, warning, deposit]


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

        query.data = f"al:t:{state['id']}:2:0"
        await lists.list_callback(update, context)
        assert state["rows"][2]["selected"] is True
        assert query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[2][0].callback_data.endswith(":2:1")

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


def test_button_labels_have_star_then_poll_then_name_and_short_status():
    state = sample_snapshot()
    _, markup = lists._render(state)
    labels = [row[0].text for row in markup.inline_keyboard[:len(state["rows"])]]
    assert all(len(label) <= 72 for label in labels)
    assert labels[0].startswith("☐ — ❌ Дима — ❗ (5/5)")
    assert labels[1].startswith("☐ — ⭐ Аня — ∞ (8/15)")
    assert labels[2].startswith("☐ — ❔ Лиза — разово")
    assert labels[3].startswith("☐ — 💵 🆕 @new_parent — новый")
    state["rows"][0]["selected"] = True
    _, markup = lists._render(state)
    assert markup.inline_keyboard[0][0].text.startswith("✅ — ❌ Дима")


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



def test_unlimited_displays_real_used_count_out_of_group_guideline():
    columns = lists._header(COLUMNS)
    row = student("Лена", sub="Безлимит", used="7", unused="", calendar="3")
    for group in lists.GROUPS:
        status = lists.compact_subscription_status(row, columns, date(2026, 11, 11), group)
        effective = 15 if group["key"].startswith("69_") else 10
        assert status == f"∞ (7/{effective})"


def test_expired_pass_keeps_usage_and_red_exclamation():
    columns = lists._header(COLUMNS)
    group = lists.GROUPS[2]
    expired_by_date = student("Дима", sub="Абон 8", limit="8",
                              used="7", unused="1", end="01.11.2026")
    exhausted = student("Дима", sub="Абон 8", limit="8",
                        used="8", unused="0")
    assert lists.compact_subscription_status(
        expired_by_date, columns, date(2026, 11, 11), group
    ) == "❗ (7/8) (срок истёк)"
    assert lists.compact_subscription_status(
        exhausted, columns, date(2026, 11, 11), group
    ) == "❗ (8/8)"
    last_day = student("Дима", sub="Абон 8", limit="8",
                       used="7", unused="1", end="11.11.2026")
    assert "❗" not in lists.compact_subscription_status(
        last_day, columns, date(2026, 11, 11), group
    )


def test_unpaid_deposit_marks_yellow_and_survives_copy_with_amount():
    group = dict(lists.GROUPS[3])
    roster = [COLUMNS,
              student("Жаркова Майя", group=group["sheet_group"],
                      sub="Абон 8", limit="8", used="4", unused="4",
                      deposit="не оплач 1250"),
              student("Заливохина Дарина", group=group["sheet_group"],
                      sub="Безлимит", used="5", unused="", deposit=""),
              student("Стрищенко Глеб", group=group["sheet_group"],
                      sub="Разово", deposit="не оплач")]
    state = lists.build_lesson_snapshot(
        group, date(2026, 11, 11), roster, [], [], [])
    names = [r["name"] for r in state["rows"]]
    assert names == ["Жаркова Майя", "Заливохина Дарина", "Стрищенко Глеб"]
    assert state["rows"][0]["unpaid"] == "не оплачено 1250"
    assert state["rows"][2]["unpaid"] == "не оплачено"
    assert "⚠️ не оплачено 1250" in lists._button_label(state["rows"][0])
    assert "⚠️ не оплачено" in lists._button_label(state["rows"][2])
    assert "⚠️" not in lists._button_label(state["rows"][1])
    state["rows"][0]["selected"] = True
    state["rows"][1]["selected"] = True
    state["rows"][2]["selected"] = True
    exported = lists._export(state)
    assert "1. Жаркова Майя (не оплачено 1250)" in exported
    assert "2. Заливохина Дарина" in exported
    assert "3. Стрищенко Глеб (не оплачено)" in exported
    assert "Оплаты:" not in exported and "Комментарии:" not in exported


def test_unpaid_alert_is_not_lost_if_name_is_long():
    row = {
        "selected": False, "vote": "❔ нет ответа",
        "name": "Очень Длинное Имя Со Многими Дополнительными Фамилиями",
        "status": "unused",
        "compact_status": "❗ (7/8) (срок истёк)",
        "unpaid": "не оплачено 1250",
    }
    label = lists._button_label(row)
    assert label.startswith("☐ — ❔ ")
    assert "❗" in label
    assert "⚠️ не оплачено" in label
    assert len(label) <= 72


def test_usage_format_is_identical_for_capped_and_unlimited_passes():
    columns = lists._header(COLUMNS)
    lesson = date(2026, 11, 11)
    group_69 = lists.GROUPS[2]
    assert lists.compact_subscription_status(
        student("Маша", sub="Абон 8", limit="8", used="4", unused="4"),
        columns, lesson, group_69
    ) == "(4/8)"
    assert lists.compact_subscription_status(
        student("Лиза", sub="Пробный абон 3", limit="3", used="2", unused="1"),
        columns, lesson, group_69
    ) == "(2/3)"
    assert lists.compact_subscription_status(
        student("Глеб", sub="Безлимит", used="7", unused="", calendar="8"),
        columns, lesson, group_69
    ) == "∞ (7/15)"
    assert lists.compact_subscription_status(
        student("Глеб", sub="Безлимит", used="5", unused="", calendar="5"),
        columns, lesson, lists.GROUPS[0]
    ) == "∞ (5/10)"


def test_unpaid_without_specified_amount_does_not_invent_cost():
    group = dict(lists.GROUPS[2])
    roster = [
        COLUMNS,
        student("Лиза", group=group["sheet_group"], sub="Абон 8",
                limit="8", used="4", unused="4", deposit="не оплач")
    ]
    session = lists.build_lesson_snapshot(
        group, date(2026, 11, 11), roster, [], [], []
    )
    row = session["rows"][0]
    assert row["unpaid"] == "не оплачено"
    assert "⚠️ не оплачено" in lists._button_label(row)
    assert "1250" not in lists._button_label(row)
    row["selected"] = True
    copied = lists._export(session)
    assert "Лиза (не оплачено)" in copied
    assert "1250" not in copied


def test_tick_then_untick_immediately_updates_same_student_and_export(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    state = sample_snapshot()
    query = SimpleNamespace(
        data="", answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        callback_query=query,
    )
    context = SimpleNamespace(user_data={lists.SESSION_KEY: state})
    async def scenario():
        _, first = lists._render(state)
        tick = first.inline_keyboard[0][0].callback_data
        assert tick.endswith(":0:0")
        query.data = tick
        await lists.list_callback(update, context)
        assert state["rows"][0]["selected"] is True
        assert lists._export(state).startswith("📋")
        _, second = lists._render(state)
        untick = second.inline_keyboard[0][0].callback_data
        assert untick.endswith(":0:1")
        assert tick != untick

        # A replay of the old button is ignored, even if it arrives as a new update.
        edits = query.edit_message_text.await_count
        query.data = tick
        await lists.list_callback(update, context)
        assert query.edit_message_text.await_count == edits
        assert state["rows"][0]["selected"] is True

        # An intentional immediate second tap sees the new callback data.
        query.data = untick
        await lists.list_callback(update, context)
        assert state["rows"][0]["selected"] is False
        assert lists._export(state) is None
        assert query.edit_message_text.await_count == edits + 1
        _, final = lists._render(state)
        assert final.inline_keyboard[0][0].callback_data == tick
    asyncio.run(scenario())


def test_concurrent_stale_clicks_are_serialized_and_not_double_applied(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    state = sample_snapshot()
    entered = asyncio.Event()
    release = asyncio.Event()
    edits = []

    async def edit_slow(*args, **kwargs):
        edits.append((args, kwargs))
        entered.set()
        await release.wait()

    query1 = SimpleNamespace(data=f"al:t:{state['id']}:0:0",
                              answer=AsyncMock(), edit_message_text=AsyncMock(side_effect=edit_slow))
    query2 = SimpleNamespace(data=query1.data,
                              answer=AsyncMock(), edit_message_text=AsyncMock())
    context = SimpleNamespace(user_data={lists.SESSION_KEY: state})
    def update(q):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1, type="private"),
            callback_query=q,
        )

    async def scenario():
        first = asyncio.create_task(lists.list_callback(update(query1), context))
        await entered.wait()
        second = asyncio.create_task(lists.list_callback(update(query2), context))
        release.set()
        await asyncio.gather(first, second)
        assert state["rows"][0]["selected"] is True
        assert len(edits) == 1
        query2.edit_message_text.assert_not_awaited()
    asyncio.run(scenario())


def test_failed_edit_rolls_back_attendance_state(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    state = sample_snapshot()
    query = SimpleNamespace(
        data=f"al:t:{state['id']}:0:0",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(side_effect=RuntimeError("telegram timeout")),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        callback_query=query,
    )
    context = SimpleNamespace(user_data={lists.SESSION_KEY: state})

    async def scenario():
        import pytest
        with pytest.raises(RuntimeError, match="telegram timeout"):
            await lists.list_callback(update, context)
        assert state["rows"][0]["selected"] is False
        assert lists._export(state) is None
    asyncio.run(scenario())
