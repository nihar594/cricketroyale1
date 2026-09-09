"""
Shared permission helpers.
"""

from __future__ import annotations

from telegram.error import TelegramError
from telegram.ext import ContextTypes


async def is_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """True if user_id is an administrator or the creator of chat_id.
    Always False for private chats or on any lookup failure."""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False
