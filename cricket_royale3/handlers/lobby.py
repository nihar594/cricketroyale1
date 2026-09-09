"""
Team Match lobby: live-edited message, join/leave buttons, 2 minute timer,
auto-start once both teams have >= MIN_PLAYERS_PER_TEAM, auto-cancel otherwise.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.keyboards import captain_select_keyboard, create_team_button_keyboard, host_select_keyboard, lobby_keyboard
from utils.media import send_photo_or_text
from utils.models import MatchMode, MatchState, MatchStatus, Player, TeamKey

logger = logging.getLogger(__name__)


def _lobby_text(match: MatchState, seconds_left: int) -> str:
    host_line = f"🎪 Host: {match.host_display_name}\n\n" if match.host_display_name else ""
    lines = [
        "⚔️ *TEAM REGISTRATION OPEN!* ⚔️",
        "",
        host_line.rstrip(),
        f"Players, choose your sides! You have {seconds_left}s to join. ⏳",
        "(Host can type /rejoin to extend 30s, or use /add or /remove)",
        "",
        f"🅰️ *Team A* ({match.team_a.size})",
        *match.team_a.roster_lines(),
        "",
        f"🅱️ *Team B* ({match.team_b.size})",
        *match.team_b.roster_lines(),
        "",
        f"Need at least {config.MIN_PLAYERS_PER_TEAM} players per team to start.",
    ]
    return "\n".join(line for line in lines if line != "")


async def create_team_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point from the /start menu: ask who wants to be Host first,
    before any lobby joining begins."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ("group", "supergroup"):
        await context.bot.send_message(
            chat_id=chat.id,
            text="👥 Team Match needs a group chat. Add me to a group and try again!",
        )
        return

    db = context.application.bot_data.get("db")
    if db is not None:
        await db.upsert_chat(chat.id, chat.type, chat.title)

    match = MatchState(chat_id=chat.id, mode=MatchMode.TEAM, initiator_id=user.id, creator_id=None)
    match.chat_username = chat.username
    match.status = MatchStatus.AWAITING_HOST
    store = get_store(context)
    store.set(match)

    msg = await send_photo_or_text(
        context=context,
        chat_id=chat.id,
        photo_path=config.team_match_banner(),
        caption=(
            "👥 *Team Match!*\n\n"
            "One person needs to become Host for this match. The Host can "
            "cancel the lobby and force-end the match.\n\n"
            "Tap the button below to become Host 👇"
        ),
        reply_markup=host_select_keyboard(),
    )
    match.banner_message_id = msg.message_id


async def host_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await query.answer("This match no longer exists -- start a new one with /start.", show_alert=True)
        return

    if match.creator_id is not None:
        await query.answer(
            f"This match already has a Host: {match.host_display_name}!", show_alert=True
        )
        return

    if match.status != MatchStatus.AWAITING_HOST:
        await query.answer("Host selection isn't active right now.", show_alert=True)
        return

    from handlers.owner import is_banned
    if await is_banned(context, user.id):
        await query.answer("You've been banned from Cricket Royale.", show_alert=True)
        return

    match.creator_id = user.id
    match.host_display_name = f"@{user.username}" if user.username else user.first_name
    await query.answer("🎪 You are now the Host of this match!")

    from utils.scoreboard import escape_md
    confirmed_text = f"✅ *Host confirmed:* {escape_md(match.host_display_name)}\n\nOpening team registration..."
    try:
        if query.message.photo:
            await query.edit_message_caption(caption=confirmed_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
        else:
            await query.edit_message_text(text=confirmed_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
    except TelegramError as exc:
        logger.debug("Could not edit host-claim banner message: %s", exc)

    await _begin_lobby(match, context)


async def create_team_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Button alternative to typing /create_team manually -- same effect."""
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.mode is not MatchMode.TEAM or match.status != MatchStatus.AWAITING_CREATE_TEAM:
        await query.answer("There's no Team Match waiting to be opened right now.", show_alert=True)
        return

    if user.id != match.creator_id:
        await query.answer("Only the Host can open team registration.", show_alert=True)
        return

    await query.answer("Opening team registration...")
    await _begin_lobby(match, context)


async def create_team_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Host-only: opens team registration (the actual join lobby) after
    the Host has been confirmed."""
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.mode is not MatchMode.TEAM or match.status != MatchStatus.AWAITING_CREATE_TEAM:
        await update.message.reply_text("There's no Team Match waiting to be opened right now.")
        return

    if user.id != match.creator_id:
        await update.message.reply_text("Only the Host can open team registration.")
        return

    await _begin_lobby(match, context)


async def host_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.AWAITING_HOST:
        await query.answer("There's nothing to cancel right now.", show_alert=True)
        return

    if user.id != match.initiator_id:
        await query.answer("Only whoever started this Team Match can cancel it.", show_alert=True)
        return

    match.status = MatchStatus.CANCELLED
    store.remove(chat.id)
    await query.answer("Cancelled.")
    try:
        if query.message.photo:
            await query.edit_message_caption(caption="🛑 *Team Match Cancelled.*", parse_mode=ParseMode.MARKDOWN, reply_markup=None)
        else:
            await query.edit_message_text(text="🛑 *Team Match Cancelled.*", parse_mode=ParseMode.MARKDOWN, reply_markup=None)
    except TelegramError as exc:
        logger.debug("Could not edit cancelled host-select message: %s", exc)


async def _begin_lobby(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called once a Host has claimed the match -- sends the live join
    lobby as a fresh message (so future edits are always plain text, even
    though the original Host-select message may have been a photo) and
    starts the 2-minute timer."""
    match.status = MatchStatus.LOBBY
    match.created_at = time.time()

    msg = await context.bot.send_message(
        chat_id=match.chat_id,
        text=_lobby_text(match, config.LOBBY_TIMEOUT_SECONDS),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=lobby_keyboard(),
    )
    match.lobby_message_id = msg.message_id

    job_name = f"lobby_timeout_{match.chat_id}_{match.match_id}"
    match.lobby_job_name = job_name
    context.job_queue.run_once(
        lobby_timeout_callback,
        when=config.LOBBY_TIMEOUT_SECONDS,
        chat_id=match.chat_id,
        name=job_name,
        data={"chat_id": match.chat_id, "match_id": match.match_id},
    )


async def _refresh_lobby_message(match: MatchState, context: ContextTypes.DEFAULT_TYPE, seconds_left: int) -> None:
    if match.lobby_message_id is None:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=match.chat_id,
            message_id=match.lobby_message_id,
            text=_lobby_text(match, seconds_left),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=lobby_keyboard(),
        )
    except TelegramError as exc:
        logger.debug("Could not edit lobby message: %s", exc)


async def rejoin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Host-only: extends the current team-registration window by another
    30 seconds."""
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.LOBBY:
        await update.message.reply_text("There's no open team registration to extend right now.")
        return

    if user.id != match.creator_id:
        await update.message.reply_text("Only the Host can extend registration.")
        return

    if match.lobby_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.lobby_job_name):
            job.schedule_removal()

    match.created_at = time.time()
    job_name = f"lobby_timeout_{match.chat_id}_{match.match_id}_{int(match.created_at)}"
    match.lobby_job_name = job_name
    context.job_queue.run_once(
        lobby_timeout_callback,
        when=config.LOBBY_REJOIN_EXTENSION_SECONDS,
        chat_id=match.chat_id,
        name=job_name,
        data={"chat_id": match.chat_id, "match_id": match.match_id},
    )

    await update.message.reply_text(f"⏳ Registration extended by {config.LOBBY_REJOIN_EXTENSION_SECONDS}s!")
    await _refresh_lobby_message(match, context, config.LOBBY_REJOIN_EXTENSION_SECONDS)


async def lobby_force_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Host-only: skips the rest of the wait and starts immediately, as
    long as both teams already meet the minimum player count."""
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.LOBBY:
        await query.answer("No active lobby right now.", show_alert=True)
        return

    if user.id != match.creator_id:
        await query.answer("Only the Host can force start.", show_alert=True)
        return

    if match.team_a.size < config.MIN_PLAYERS_PER_TEAM or match.team_b.size < config.MIN_PLAYERS_PER_TEAM:
        await query.answer(
            f"Need at least {config.MIN_PLAYERS_PER_TEAM} players per team to force start.",
            show_alert=True,
        )
        return

    await query.answer("⚡ Forcing start!")
    await _auto_start_match(match, context)


async def lobby_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.LOBBY:
        await query.answer("No active lobby right now.", show_alert=True)
        return

    from handlers.owner import is_banned
    if await is_banned(context, user.id):
        await query.answer("You've been banned from Cricket Royale.", show_alert=True)
        return

    data_parts = query.data.split(":")
    team_letter = data_parts[2]  # "A" or "B"
    team_key = TeamKey.A if team_letter == "A" else TeamKey.B
    target_team = match.team(team_key)
    other_team = match.team(team_key.other)

    if user.id in other_team.players:
        await query.answer("You're already in the other team!", show_alert=True)
        return

    if user.id in target_team.players:
        await query.answer("You're already in this team!", show_alert=True)
        return

    if target_team.size >= config.MAX_PLAYERS_PER_TEAM:
        await query.answer("That team is full.", show_alert=True)
        return

    new_player = Player(user_id=user.id, username=user.username, first_name=user.first_name)
    target_team.add_player(new_player)
    await query.answer(f"Joined {target_team.name}!")

    # Plain first name only in the group message -- no @username tag/mention.
    join_emoji = "🔴" if team_key is TeamKey.A else "🔵"
    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"{join_emoji} {new_player.first_name} joined Team {team_key.value}!",
    )

    seconds_left = max(
        0, int(config.LOBBY_TIMEOUT_SECONDS - (time.time() - match.created_at))
    )
    await _refresh_lobby_message(match, context, seconds_left)

    if match.team_a.size >= config.MIN_PLAYERS_PER_TEAM and match.team_b.size >= config.MIN_PLAYERS_PER_TEAM:
        await _auto_start_match(match, context)


async def lobby_leave_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.LOBBY:
        await query.answer("No active lobby right now.", show_alert=True)
        return

    team = match.find_team_of(user.id)
    if team is None:
        await query.answer("You're not in this lobby.", show_alert=True)
        return

    player = team.players.get(user.id)
    player_name = player.first_name if player else (user.first_name or "Player")

    team.remove_player(user.id)
    await query.answer("You left the lobby.")

    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"🚪 {player_name} left the lobby.",
    )

    seconds_left = max(
        0, int(config.LOBBY_TIMEOUT_SECONDS - (time.time() - match.created_at))
    )
    await _refresh_lobby_message(match, context, seconds_left)


async def _cancel_lobby(match: MatchState, context: ContextTypes.DEFAULT_TYPE, reason: str) -> None:
    match.status = MatchStatus.CANCELLED
    store = get_store(context)
    if match.lobby_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.lobby_job_name):
            job.schedule_removal()
    try:
        if match.lobby_message_id:
            await context.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.lobby_message_id,
                text=f"🛑 *Team Match Lobby Cancelled*\n\n{reason}",
                parse_mode=ParseMode.MARKDOWN,
            )
    except TelegramError as exc:
        logger.debug("Could not edit cancelled lobby message: %s", exc)
    store.remove(match.chat_id)


async def lobby_timeout_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    store = get_store(context)
    match = store.get(chat_id)

    if match is None or match.status != MatchStatus.LOBBY:
        return  # already started, cancelled, or replaced

    if match.team_a.size < config.MIN_PLAYERS_PER_TEAM or match.team_b.size < config.MIN_PLAYERS_PER_TEAM:
        await _cancel_lobby(
            match,
            context,
            reason=(
                f"Not enough players joined (need {config.MIN_PLAYERS_PER_TEAM}+ per team). "
                "Start a new Team Match with /start."
            ),
        )
    else:
        await _auto_start_match(match, context)


async def _auto_start_match(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    if match.status != MatchStatus.LOBBY:
        return
    match.status = MatchStatus.CAPTAIN_SELECT
    if match.lobby_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.lobby_job_name):
            job.schedule_removal()

    text = (
        "✅ *Both teams are ready!* The lobby is now locked.\n\n"
        f"🅰️ *Team A* ({match.team_a.size})\n" + "\n".join(match.team_a.roster_lines()) + "\n\n"
        f"🅱️ *Team B* ({match.team_b.size})\n" + "\n".join(match.team_b.roster_lines()) + "\n\n"
        "🧢 Each team must now pick a captain. Only members of a team may "
        "become that team's captain."
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
        else:
            await context.bot.send_message(
                chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN,
                reply_markup=captain_select_keyboard(),
            )
    except TelegramError as exc:
        logger.warning("Failed to announce match start: %s", exc)
