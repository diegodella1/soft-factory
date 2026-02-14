"""Telegram bot handlers for FactoryBot."""

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

from bot.config import TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID
from bot.router import handle_command, route_message

log = logging.getLogger(__name__)

# Max Telegram message length
MAX_MSG_LEN = 4096


def _is_owner(update: Update) -> bool:
    """Check if the message is from the authorized owner."""
    return update.effective_user and update.effective_user.id == TELEGRAM_OWNER_ID


async def _send(update: Update, text: str):
    """Send a message, splitting if too long. Uses Markdown parse mode."""
    chat_id = update.effective_chat.id
    # Split long messages
    while text:
        chunk = text[:MAX_MSG_LEN]
        text = text[MAX_MSG_LEN:]
        try:
            await update.get_bot().send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            # Fallback without markdown if parsing fails
            await update.get_bot().send_message(
                chat_id=chat_id,
                text=chunk,
            )


def _make_send_fn(update: Update):
    """Create a send function bound to the current chat."""
    async def send(text: str):
        await _send(update, text)
    return send


async def _cmd_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all slash commands."""
    if not _is_owner(update):
        return

    if not update.message:
        return
    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    command = parts[0].lstrip("/").split("@")[0]  # strip bot username
    args = parts[1] if len(parts) > 1 else ""

    log.info("Command: /%s %s", command, args[:50])
    await handle_command(command, args, _make_send_fn(update))


async def _text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages (natural language)."""
    if not _is_owner(update):
        return

    text = update.message.text
    if not text:
        return

    log.info("Text message: %s", text[:80])
    await route_message(text, _make_send_fn(update))


async def _file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads (images, documents)."""
    if not _is_owner(update):
        return

    await _send(
        update,
        "Recibí tu archivo. Por ahora solo proceso mensajes de texto. "
        "Describime lo que querés y lo resolvemos juntos."
    )


def build_app() -> Application:
    """Build and configure the Telegram bot application."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    commands = ["new", "projects", "revisit", "status", "approve", "pause", "resume", "start"]
    for cmd in commands:
        app.add_handler(CommandHandler(cmd, _cmd_handler))

    # Text message handler (natural language)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text_handler))

    # File handler
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.ALL,
        _file_handler,
    ))

    log.info("Telegram bot configured with %d command handlers", len(commands))
    return app
