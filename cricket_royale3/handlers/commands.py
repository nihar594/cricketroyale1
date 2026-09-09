"""
/endmatch (alias /endgame) - group-admin-only, with a Confirm End / Cancel
button before anything actually happens.
/checkassets - debug command to see which media files are found vs missing.
Also holds the global error handler.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.keyboards import end_confirm_keyboard
from utils.models import MatchMode, MatchStatus
from utils.permissions import is_group_admin

logger = logging.getLogger(__name__)


async def checkassets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists every media asset the bot looks for and whether it currently
    finds a matching file on disk -- handy for tracking down a GIF that
    isn't showing up."""
    assets = [
        ("banner", config.banner_image()),
        ("team_match_banner", config.team_match_banner()),
        ("solo_match_banner", config.solo_match_banner()),
        ("player_of_match_banner", config.player_of_match_banner()),
        ("scoreboard_banner", config.scoreboard_banner()),
        ("welcome", config.welcome_gif()),
        ("toss", config.toss_gif()),
        ("wicket", config.wicket_gif()),
        ("bowling", config.bowling_gif()),
        ("ball_delivered", config.ball_delivered_gif()),
        ("fifty", config.fifty_gif()),
        ("century", config.century_gif()),
        ("duck", config.duck_gif()),
        ("batting (owner-only)", config.batting_gif()),
        ("non_striker", config.non_striker_gif()),
    ]
    for n in range(7):
        assets.append((str(n), config.run_gif(n)))

    lines = ["🔍 *Asset Check*", ""]
    for name, path in assets:
        mark = "✅" if path.exists() else "❌"
        lines.append(f"{mark} `{name}` → `{path.name}`")

    missing = [name for name, path in assets if not path.exists()]
    lines.append("")
    if missing:
        lines.append(f"⚠️ Missing: {', '.join(missing)}")
        lines.append(f"Expected folder: `{config.GIFS_DIR}` (banners go in `{config.ASSETS_DIR}` directly).")
    else:
        lines.append("🎉 All assets found!")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anyone in the chat can run this any time to see the live team
    scorecard for the current Team Match -- both teams' batting and
    bowling figures, plus the chase/target block once the 2nd innings
    is underway. Solo Match (Royale mode) has its own /soloscore."""
    chat = update.effective_chat
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.mode is not MatchMode.TEAM:
        await update.message.reply_text("There's no active Team Match in this chat.")
        return

    if match.status not in (MatchStatus.IN_PROGRESS, MatchStatus.AWAITING_LINEUP):
        await update.message.reply_text("The match hasn't started yet -- no score to show.")
        return

    if match.batting_team_key is None:
        await update.message.reply_text("The match hasn't started yet -- no score to show.")
        return

    from utils.scoreboard import format_team_scorecard
    from utils.media import send_photo_or_text

    scorecard_text = format_team_scorecard(match)

    # Telegram photo captions cap out at 1024 characters -- a full
    # scorecard with every player's batting + bowling spells can easily
    # exceed that, so the banner goes out with just a short caption and
    # the full scorecard follows as its own message (never truncated).
    if len(scorecard_text) <= 1024:
        await send_photo_or_text(
            context=context,
            chat_id=chat.id,
            photo_path=config.scoreboard_banner(),
            caption=scorecard_text,
        )
    else:
        await send_photo_or_text(
            context=context,
            chat_id=chat.id,
            photo_path=config.scoreboard_banner(),
            caption="📊 *SCORE BOARD*",
        )
        await update.message.reply_text(scorecard_text, parse_mode="Markdown")


async def _is_authorized_to_end(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    from handlers.owner import is_owner
    if is_owner(user_id):
        return True
    return await is_group_admin(context, chat_id, user_id)


async def endmatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active match or lobby in this chat.")
        return

    if not await _is_authorized_to_end(context, chat.id, user.id):
        await update.message.reply_text("Only group admins can end the match.")
        return

    await update.message.reply_text(
        "⚠️ *End this match?*\nThis can't be undone.",
        parse_mode="Markdown",
        reply_markup=end_confirm_keyboard(),
    )


async def endmatch_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await query.answer("There's no active match or lobby anymore.", show_alert=True)
        try:
            await query.edit_message_text("This match no longer exists.")
        except TelegramError:
            pass
        return

    if not await _is_authorized_to_end(context, chat.id, user.id):
        await query.answer("Only group admins can end the match.", show_alert=True)
        return

    await query.answer("Match ended.")
    await _perform_end_match(match, context, user, query.message)


async def endmatch_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user

    if not await _is_authorized_to_end(context, chat.id, user.id):
        await query.answer("Only group admins can do this.", show_alert=True)
        return

    await query.answer("Cancelled.")
    try:
        await query.edit_message_text("✅ Match continues -- nothing was ended.")
    except TelegramError as exc:
        logger.debug("Could not edit cancel confirmation message: %s", exc)


async def _perform_end_match(match, context: ContextTypes.DEFAULT_TYPE, user, confirm_message) -> None:
    store = get_store(context)

    if match.lobby_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.lobby_job_name):
            job.schedule_removal()

    if match.spamfree_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.spamfree_job_name):
            job.schedule_removal()

    if match.mode is MatchMode.SOLO:
        from handlers.solo import cancel_bowler_timeout_jobs, cancel_batter_timeout_jobs
        cancel_bowler_timeout_jobs(match, context)
        cancel_batter_timeout_jobs(match, context)
    elif match.mode is MatchMode.TEAM:
        from handlers.team_timeout import cancel_bowler_timeout_jobs, cancel_batter_timeout_jobs
        cancel_bowler_timeout_jobs(match, context)
        cancel_batter_timeout_jobs(match, context)

    was_in_progress = match.status == MatchStatus.IN_PROGRESS
    match.status = MatchStatus.CANCELLED
    store.remove(match.chat_id)

    if was_in_progress:
        if match.mode is MatchMode.SOLO and match.striker_id:
            batter = match.royale_players.get(match.striker_id)
            batter_line = f"{batter.display_name}: {batter.runs} ({batter.balls_faced}b)" if batter else ""
            result_text = f"🛑 Match ended early by {user.first_name}.\n\nCurrent batter: {batter_line}"
        else:
            result_text = (
                f"🛑 Match ended early by {user.first_name}.\n\n"
                f"Final score: {match.batting_team.name} {match.score}/{match.wickets} "
                f"({match.current_over}.{match.current_ball}/{match.overs} ov)"
            )
    else:
        result_text = f"🛑 Match/lobby ended by {user.first_name}."

    try:
        await confirm_message.edit_text(result_text)
    except TelegramError:
        await context.bot.send_message(chat_id=match.chat_id, text=result_text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    # Belt-and-suspenders: some hosting setups swallow logging output in
    # ways that are non-obvious locally, so also print a plain traceback
    # directly to stdout/stderr.
    import traceback
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
