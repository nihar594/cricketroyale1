"""
Team Match AFK-timeout system. Mirrors handlers/solo.py's Royale-mode
response clock (10s / 30s reminders, 60s final) but with Team Match's own
consequences instead of removing the player from the match entirely:

  - Batter doesn't play their shot within 60s -> they're given OUT, and a
    5-run penalty is deducted from both the team's score and their own
    individual score. The batting captain/Host picks the next batter.

  - Bowler doesn't bowl within 60s -> no elimination, but the batting team
    is awarded a 5-run penalty. The bowling captain/Host picks a new
    bowler to continue the same over.

Neither case consumes a ball -- the over/ball counters don't move, since
no delivery actually happened.
"""

from __future__ import annotations

import logging
import time

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from handlers.start import get_store
from utils.models import MatchMode, MatchState, MatchStatus

logger = logging.getLogger(__name__)

_REMINDER_1_DELAY = 10   # fires with 50s left
_REMINDER_2_DELAY = 30   # fires with 30s left
_FINAL_DELAY = 60

PENALTY_RUNS = 5


def _mention(player) -> str:
    """A Markdown mention that actually notifies/tags the player, even if
    they don't have a public @username."""
    return f"[{player.first_name}](tg://user?id={player.user_id})"


def _cancel_named_jobs(context: ContextTypes.DEFAULT_TYPE, job_names: list) -> None:
    for name in job_names:
        for job in context.application.job_queue.get_jobs_by_name(name):
            job.schedule_removal()


def schedule_bowler_timeout(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    if match.mode is not MatchMode.TEAM or not match.bowler_id:
        return
    cancel_bowler_timeout_jobs(match, context)
    base = f"team_bowler_{match.chat_id}_{match.match_id}_{match.bowler_id}_{time.time()}"
    names = [f"{base}_r1", f"{base}_r2", f"{base}_final"]
    match.bowler_timeout_job_names = names
    data = {"chat_id": match.chat_id, "user_id": match.bowler_id}
    context.job_queue.run_once(_bowler_reminder_1, when=_REMINDER_1_DELAY, chat_id=match.chat_id, name=names[0], data=data)
    context.job_queue.run_once(_bowler_reminder_2, when=_REMINDER_2_DELAY, chat_id=match.chat_id, name=names[1], data=data)
    context.job_queue.run_once(_bowler_final_timeout, when=_FINAL_DELAY, chat_id=match.chat_id, name=names[2], data=data)


def cancel_bowler_timeout_jobs(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    _cancel_named_jobs(context, match.bowler_timeout_job_names)
    match.bowler_timeout_job_names = []


def schedule_batter_timeout(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    if match.mode is not MatchMode.TEAM or not match.striker_id:
        return
    cancel_batter_timeout_jobs(match, context)
    base = f"team_batter_{match.chat_id}_{match.match_id}_{match.striker_id}_{time.time()}"
    names = [f"{base}_r1", f"{base}_r2", f"{base}_final"]
    match.batter_timeout_job_names = names
    data = {"chat_id": match.chat_id, "user_id": match.striker_id}
    context.job_queue.run_once(_batter_reminder_1, when=_REMINDER_1_DELAY, chat_id=match.chat_id, name=names[0], data=data)
    context.job_queue.run_once(_batter_reminder_2, when=_REMINDER_2_DELAY, chat_id=match.chat_id, name=names[1], data=data)
    context.job_queue.run_once(_batter_final_timeout, when=_FINAL_DELAY, chat_id=match.chat_id, name=names[2], data=data)


def cancel_batter_timeout_jobs(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    _cancel_named_jobs(context, match.batter_timeout_job_names)
    match.batter_timeout_job_names = []


# --------------------------------------------------------------------------
# Bowler reminders + final timeout
# --------------------------------------------------------------------------
async def _bowler_reminder_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_bowler_reminder(context, seconds_left=50)


async def _bowler_reminder_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_bowler_reminder(context, seconds_left=30)


async def _send_bowler_reminder(context: ContextTypes.DEFAULT_TYPE, seconds_left: int) -> None:
    data = context.job.data
    store = get_store(context)
    match = store.get(data["chat_id"])
    if match is None or match.mode is not MatchMode.TEAM:
        return
    if not match.awaiting_bowler_input or match.bowler_id != data["user_id"]:
        return
    bowler = match.bowling_team.players.get(data["user_id"])
    if bowler is None:
        return
    text = f"⏳ *{seconds_left} seconds left!* {_mention(bowler)} — send your number in my DM! 🥎"
    try:
        await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as exc:
        logger.debug("Could not send Team Match bowler reminder: %s", exc)


async def _bowler_final_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    store = get_store(context)
    match = store.get(data["chat_id"])
    if match is None or match.mode is not MatchMode.TEAM:
        return
    if not match.awaiting_bowler_input or match.bowler_id != data["user_id"]:
        return
    await _penalize_afk_bowler(match, context, data["user_id"])


async def _penalize_afk_bowler(match: MatchState, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    bowling_team = match.bowling_team
    bowler = bowling_team.players.get(user_id)
    if bowler is None:
        return

    cancel_bowler_timeout_jobs(match, context)
    cancel_batter_timeout_jobs(match, context)
    match.awaiting_bowler_input = False
    match.awaiting_batter_input = False
    match.pending_bowler_number = None

    # +5 to the batting team only -- no ball bowled, no delivery, no
    # individual batter/bowler stat changes, over/ball counters untouched.
    match.score += PENALTY_RUNS

    match.bowler_id = None

    text = (
        f"⏳ *TIME'S UP!* ' {bowler.display_name} ! timed out! ❌\n"
        f"📈 *PENALTY: +{PENALTY_RUNS} Runs to Batting Team!*\n\n"
        f"Captain/Host, please select a NEW bowler to continue the over using /bowling [number]."
    )
    await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)

    from utils.scoreboard import format_scoreboard
    await context.bot.send_message(
        chat_id=match.chat_id, text=format_scoreboard(match), parse_mode=ParseMode.MARKDOWN
    )


# --------------------------------------------------------------------------
# Batter reminders + final timeout
# --------------------------------------------------------------------------
async def _batter_reminder_1(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_batter_reminder(context, seconds_left=50)


async def _batter_reminder_2(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_batter_reminder(context, seconds_left=30)


async def _send_batter_reminder(context: ContextTypes.DEFAULT_TYPE, seconds_left: int) -> None:
    data = context.job.data
    store = get_store(context)
    match = store.get(data["chat_id"])
    if match is None or match.mode is not MatchMode.TEAM:
        return
    if not match.awaiting_batter_input or match.striker_id != data["user_id"]:
        return
    batter = match.batting_team.players.get(data["user_id"])
    if batter is None:
        return
    text = f"⏳ *{seconds_left} seconds left!* {_mention(batter)} — send your number in group from 1-6! 🏏"
    try:
        await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as exc:
        logger.debug("Could not send Team Match batter reminder: %s", exc)


async def _batter_final_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    store = get_store(context)
    match = store.get(data["chat_id"])
    if match is None or match.mode is not MatchMode.TEAM:
        return
    if not match.awaiting_batter_input or match.striker_id != data["user_id"]:
        return
    await _penalize_afk_batter(match, context, data["user_id"])


async def _penalize_afk_batter(match: MatchState, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    batting_team = match.batting_team
    batter = batting_team.players.get(user_id)
    if batter is None:
        return

    cancel_bowler_timeout_jobs(match, context)
    cancel_batter_timeout_jobs(match, context)
    match.awaiting_bowler_input = False
    match.awaiting_batter_input = False
    match.pending_bowler_number = None

    # -5 to both the team score and the batter's own score (floored at 0),
    # then the batter is given out. No ball bowled, no delivery, over/ball
    # counters untouched.
    match.score = max(0, match.score - PENALTY_RUNS)
    batter.runs = max(0, batter.runs - PENALTY_RUNS)
    batter.is_out = True
    match.wickets += 1

    text = (
        f"⏳ *TIME'S UP!* {batter.display_name} was AFK for 60 seconds! ❌\n"
        f"📉 *PENALTY: -{PENALTY_RUNS} Runs to the team and batter!* They are OUT!\n\n"
        f"🏏 Captain/Host, please select the next batter using /batting [number]."
    )
    await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)

    from utils.scoreboard import format_scoreboard
    await context.bot.send_message(
        chat_id=match.chat_id, text=format_scoreboard(match), parse_mode=ParseMode.MARKDOWN
    )

    # The batter who was just out leaves the crease; the not-out partner
    # (if any) stays on strike, leaving the non-striker slot open for the
    # incoming batter -- same handling as a normal mid-over wicket.
    if match.non_striker_id:
        match.striker_id = match.non_striker_id
        match.non_striker_id = None
    else:
        match.striker_id = None

    all_out = match.wickets >= batting_team.size - 1
    overs_done = match.current_over >= match.overs
    target_reached = match.target is not None and match.score >= match.target

    if target_reached or overs_done or all_out:
        from handlers.gameplay import end_innings
        await end_innings(match, context)
        return

    cap = batting_team.players[batting_team.captain_id]
    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"🏏 {cap.display_name}, use /batting to send in the next batter.",
    )

    from handlers.gameplay import maybe_ready_to_bowl
    await maybe_ready_to_bowl(match, context)
