import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import info_handler as info


def make_location_context():
    query = SimpleNamespace(
        data="info|location",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, full_name="Admin", username="admin"),
        effective_chat=SimpleNamespace(id=123, type="private"),
    )
    context = SimpleNamespace(bot=SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(),
    ))
    return update, context


def test_location_photo_sent_to_chat_with_caption_and_maps_button(monkeypatch):
    monkeypatch.delenv("KARINA_ID", raising=False)
    monkeypatch.setenv("LOCATION_PHOTO_FILE_ID", "telegram-file-id-of-entrance")
    update, context = make_location_context()
    asyncio.run(info.info_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    args = context.bot.send_photo.await_args.kwargs
    assert args["chat_id"] == 123
    assert args["photo"] == "telegram-file-id-of-entrance"
    assert "Scenia Bay" in args["caption"]
    assert "Shama Book Bakery" in args["caption"]
    assert "вход в наш зал" in args["caption"]
    assert args["parse_mode"] == "HTML"
    assert '📍 <b>Локация</b>: <a href="' in args["caption"]
    assert '">Смотреть на карте</a>' in args["caption"]
    assert info.LOCATION_MAPS_URL in args["caption"]
    buttons = args["reply_markup"].inline_keyboard
    assert buttons[0][0].url.startswith("https://maps.app.goo.gl/")
    assert not update.callback_query.edit_message_text.await_count
    assert "t.me/c/" not in args["caption"]


def test_location_without_photo_id_keeps_map_and_no_group_links(monkeypatch):
    monkeypatch.delenv("KARINA_ID", raising=False)
    monkeypatch.delenv("LOCATION_PHOTO_FILE_ID", raising=False)
    update, context = make_location_context()
    asyncio.run(info.info_callback(update, context))

    context.bot.send_photo.assert_not_awaited()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "Как найти зал" in text
    assert "Фото входа" in text
    assert "t.me/c/" not in text
    buttons = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert buttons[0][0].url == info.LOCATION_MAPS_URL


def command_update(user_id, chat_type="private", photo=None):
    original = SimpleNamespace(photo=photo) if photo is not None else None
    message = SimpleNamespace(reply_to_message=original, reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=user_id, type=chat_type),
        message=message,
    )
    return update


def test_admin_reply_to_existing_photo_returns_file_id_without_caption(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("KARINA_ID", "2")
    update = command_update(2, photo=[
        SimpleNamespace(file_id="thumbnail-id"),
        SimpleNamespace(file_id="photo-original-id"),
    ])
    asyncio.run(info.location_photo_id_command(update, SimpleNamespace()))
    reply = update.message.reply_text.await_args.args[0]
    assert "photo-original-id" in reply
    assert "LOCATION_PHOTO_FILE_ID" in reply
    assert "caption" not in reply
    assert "Telegram" in reply


def test_unauthorized_or_group_cannot_obtain_photo_id(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("KARINA_ID", "2")
    photo = [SimpleNamespace(file_id="private-photo-id")]
    unauthorized = command_update(99, photo=photo)
    asyncio.run(info.location_photo_id_command(unauthorized, SimpleNamespace()))
    text = unauthorized.message.reply_text.await_args.args[0]
    assert "только администратору" in text
    assert "private-photo-id" not in text

    group = command_update(1, chat_type="supergroup", photo=photo)
    asyncio.run(info.location_photo_id_command(group, SimpleNamespace()))
    text = group.message.reply_text.await_args.args[0]
    assert "личном чате" in text
    assert "private-photo-id" not in text


def test_admin_is_told_to_reply_to_photo(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    update = command_update(1)
    asyncio.run(info.location_photo_id_command(update, SimpleNamespace()))
    text = update.message.reply_text.await_args.args[0]
    assert "Ответить" in text
    assert "/location_photo_id" in text


def test_location_helper_is_registered_and_action_deduplicated():
    from pathlib import Path
    source = Path("main.py").read_text()
    assert 'CommandHandler("location_photo_id", location_photo_id_command)' in source
    assert '"/location_photo_id"' in source


def test_location_still_shows_map_when_photo_send_fails(monkeypatch):
    from telegram.error import BadRequest

    monkeypatch.delenv("KARINA_ID", raising=False)
    monkeypatch.setenv("LOCATION_PHOTO_FILE_ID", "old-photo-id")
    update, context = make_location_context()
    context.bot.send_photo.side_effect = BadRequest("invalid file identifier")
    asyncio.run(info.info_callback(update, context))
    context.bot.send_photo.assert_awaited_once()
    fallback = update.callback_query.edit_message_text.await_args
    assert "временно недоступно" in fallback.args[0]
    assert fallback.kwargs["reply_markup"].inline_keyboard[0][0].url == info.LOCATION_MAPS_URL
