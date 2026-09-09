"""
/start command and the main menu (Solo Match / Team Match / Leaderboard / Cancel).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
from utils.keyboards import start_menu
from utils.media import send_photo_or_text
from utils.models import MatchState, MatchMode, MatchStatus, MatchStore

logger = logging.getLogger(__name__)

WELCOME_CAPTION = (
    "🏏✨ *Welcome to Cricket Royale!* ✨🏏\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "🔥 The fast-paced multiplayer cricket experience, right here in Telegram.\n\n"
    "👇 *Choose your mode to begin:*"
)


def get_store(context: ContextTypes.DEFAULT_TYPE) -> MatchStore:
    store = context.application.bot_data.get("matches")
    if store is None:
        store = MatchStore()
        context.application.bot_data["matches"] = store
    return store


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)
    await db.upsert_chat(chat.id, chat.type, chat.title)

    from handlers.owner import is_banned
    if await is_banned(context, user.id):
        await context.bot.send_message(
            chat_id=chat.id,
            text="🚫 You've been banned from Cricket Royale and can't use this bot.",
        )
        return

    # Deep-link payload: the "Bowl Now" button opens a private chat with the
    # bot via https://t.me/<bot>?start=bowl, which Telegram turns into
    # "/start bowl" automatically -- route that straight to the delivery
    # prompt instead of showing the main menu.
    if context.args and context.args[0] == "bowl":
        from handlers.gameplay import handle_bowl_deeplink
        await handle_bowl_deeplink(update, context)
        return

    store = get_store(context)
    existing = store.get(chat.id)
    if existing is not None and existing.status not in (MatchStatus.COMPLETED, MatchStatus.CANCELLED):
        await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ There's already an active match/lobby in this chat. "
                 "Use /teams to check its status, or /endmatch to end it first.",
        )
        return

    await send_photo_or_text(
        context=context,
        chat_id=update.effective_chat.id,
        photo_path=config.banner_image(),
        caption=WELCOME_CAPTION,
        reply_markup=start_menu(),
    )


async def start_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)

    if action == "cancel":
        if query.message.caption:
            await query.edit_message_caption(caption="❌ Cancelled.")
        else:
            await query.edit_message_text("❌ Cancelled.")
        return

    if action == "leaderboard":
        from handlers.leaderboard import leaderboard_menu_callback
        await leaderboard_menu_callback(update, context)
        return

    if action in ("solo", "team"):
        from handlers.owner import is_banned, is_maintenance_mode, is_owner
        if await is_banned(context, user.id):
            await context.bot.send_message(
                chat_id=chat.id,
                text="🚫 You've been banned from Cricket Royale and can't join or start matches.",
            )
            return
        if is_maintenance_mode(context) and not is_owner(user.id):
            await context.bot.send_message(
                chat_id=chat.id,
                text="🔧 *Cricket Royale is under maintenance right now.*\nPlease check back shortly!",
                parse_mode="Markdown",
            )
            return

    existing = store.get(chat.id)
    if existing is not None and existing.status not in (MatchStatus.COMPLETED, MatchStatus.CANCELLED):
        await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ There's already an active match/lobby in this chat. "
                 "Finish or /endmatch it first.",
        )
        return

    if action == "solo":
        try:
            await query.message.delete()
        except Exception as exc:  # noqa: BLE001 - deletion is best-effort
            logger.debug("Could not delete start menu message: %s", exc)
        from handlers.solo import start_solo_match
        await start_solo_match(update, context)
        return

    if action == "team":
        try:
            await query.message.delete()
        except Exception as exc:  # noqa: BLE001 - deletion is best-effort
            logger.debug("Could not delete start menu message: %s", exc)
        from handlers.lobby import create_team_lobby
        await create_team_lobby(update, context)
        return
