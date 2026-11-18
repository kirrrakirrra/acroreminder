"""Best-effort feedback for Telegram operations that wait on Sheets or delivery.

Command results replace a single temporary message. Callback operations can use
a separate temporary message when the original message is being updated by an
existing reporting/reminder flow. Failure to display or remove progress must
never prevent the operation itself.
"""
import logging


PROGRESS_TEXT = "⏳ Собираю информацию…"


async def start_progress(message, text=PROGRESS_TEXT):
    """Reply with a temporary message after validating access."""
    try:
        return await message.reply_text(text)
    except Exception:
        logging.warning("Could not send Telegram progress placeholder", exc_info=True)
        return None


async def finish_progress(message, pending, text, **kwargs):
    """Edit the placeholder into the result, or fall back to a fresh reply."""
    if pending is not None:
        try:
            await pending.edit_text(text, **kwargs)
            return
        except Exception:
            logging.warning("Could not edit Telegram progress placeholder", exc_info=True)
            try:
                await pending.delete()
            except Exception:
                logging.debug("Could not delete stale progress placeholder", exc_info=True)
    await message.reply_text(text, **kwargs)


async def start_callback_progress(update, context, text=PROGRESS_TEXT):
    """Do not edit a callback's source message: it may be an active report/menu."""
    chat = getattr(update, "effective_chat", None)
    message = getattr(getattr(update, "callback_query", None), "message", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id is None:
        return None

    kwargs = {"chat_id": chat_id, "text": text}
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    try:
        return await context.bot.send_message(**kwargs)
    except Exception:
        logging.warning("Could not send Telegram callback progress", exc_info=True)
        return None


async def clear_callback_progress(pending, *, fallback="✅ Готово"):
    """Delete a separate progress message, replacing it if deletion fails."""
    if pending is None:
        return
    try:
        await pending.delete()
    except Exception:
        logging.warning("Could not delete Telegram callback progress", exc_info=True)
        try:
            await pending.edit_text(fallback)
        except Exception:
            logging.debug("Could not finalize Telegram callback progress", exc_info=True)
