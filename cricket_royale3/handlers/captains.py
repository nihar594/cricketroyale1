"""
Captain selection: only members of Team A can become Team A captain, and
likewise for Team B. Once both captains are set, the match moves to toss.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.start import get_store
from utils.models import MatchStatus, TeamKey
from utils.scoreboard import escape_md

logger = logging.getLogger(__name__)


async def captain_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.CAPTAIN_SELECT:
        await query.answer("Captain selection isn't active right now.", show_alert=True)
        return

    team_letter = query.data.split(":")[1]
    team_key = TeamKey.A if team_letter == "A" else TeamKey.B
    team = match.team(team_key)

    if user.id not in team.players:
        await query.answer(f"You must be a member of {team.name} to captain it.", show_alert=True)
        return

    if team.captain_id is not None:
        await query.answer(f"{team.name} already has a captain.", show_alert=True)
        return

    team.captain_id = user.id
    await query.answer(f"You are now {team.name} captain! 🧢")

    team_emoji = "🔴" if team_key is TeamKey.A else "🔵"
    player = team.players[user.id]
    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"{escape_md(player.display_name)} is now Captain of Team {team_key.value} {team_emoji}!",
        parse_mode=ParseMode.MARKDOWN,
    )

    await _refresh_captain_message(match, context)

    if match.team_a.captain_id and match.team_b.captain_id:
        from handlers.toss import start_toss
        await start_toss(match, context)


async def changecap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/changecap a <username/id>  or  /changecap b <username/id>  --
    changes that team's captain. Usable by the Host or that team's
    current captain. The new captain must already be a member of that
    same team (Team A captain can only appoint from within Team A, and
    likewise for Team B)."""
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active match or lobby in this chat.")
        return

    if not context.args or context.args[0].lower() not in ("a", "b"):
        await update.message.reply_text(
            "Usage: /changecap a <username/id>  or  /changecap b <username/id>"
        )
        return

    team_key = TeamKey.A if context.args[0].lower() == "a" else TeamKey.B
    team = match.team(team_key)

    is_host = user.id == match.creator_id
    is_current_captain = user.id == team.captain_id
    if not is_host and not is_current_captain:
        await update.message.reply_text(
            f"Only the Host or {team.name}'s current captain can change its captain."
        )
        return

    extra_arg = context.args[1] if len(context.args) >= 2 else None
    replied_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None

    if not replied_user and not extra_arg:
        await update.message.reply_text(
            f"Reply to the player's message with /changecap {team_key.value.lower()}, or "
            f"/changecap {team_key.value.lower()} @username, or "
            f"/changecap {team_key.value.lower()} <user_id>."
        )
        return

    if replied_user:
        target_user_id = replied_user.id
    elif extra_arg.lstrip("@").isdigit():
        target_user_id = int(extra_arg.lstrip("@"))
    else:
        db = context.application.bot_data.get("db")
        row = await db.get_user_by_username(extra_arg) if db else None
        if row is None:
            await update.message.reply_text(
                f"Couldn't find {extra_arg} -- they need to have used the bot at least once "
                "(e.g. /start) before you can make them captain this way. You can also reply "
                f"directly to their message with /changecap {team_key.value.lower()} instead."
            )
            return
        target_user_id = row["user_id"]

    if target_user_id not in team.players:
        await update.message.reply_text(
            f"That player isn't in {team.name} -- {team.name}'s captain can only be someone "
            f"already on {team.name}."
        )
        return

    new_captain = team.players[target_user_id]
    team.captain_id = target_user_id

    await update.message.reply_text(
        f"✅ Team {team_key.value} captain changed to {escape_md(new_captain.display_name)}!",
        parse_mode=ParseMode.MARKDOWN,
    )
    await _refresh_captain_message(match, context)


async def _refresh_captain_message(match, context: ContextTypes.DEFAULT_TYPE) -> None:
    from utils.keyboards import captain_select_keyboard

    a_cap = match.team_a.players.get(match.team_a.captain_id)
    b_cap = match.team_b.players.get(match.team_b.captain_id)
    text = (
        "🧢 *Captain Selection*\n\n"
        f"🅰️ Team A captain: {escape_md(a_cap.display_name) if a_cap else '_not chosen yet_'}\n"
        f"🅱️ Team B captain: {escape_md(b_cap.display_name) if b_cap else '_not chosen yet_'}\n\n"
        "Tap below if you're a member of that team."
    )
    try:
        if match.lobby_message_id:
            await context.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.lobby_message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=captain_select_keyboard(),
            )
    except Exception as exc:  # noqa: BLE001 - best-effort UI refresh
        logger.debug("Could not refresh captain message: %s", exc)
