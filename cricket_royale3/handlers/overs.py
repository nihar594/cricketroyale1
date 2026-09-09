"""
Overs selection (1-20), Team Match only, followed by a 15-second window
for the Host to opt into "spam-free" mode before the lineup begins.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.models import MatchMode, MatchStatus

logger = logging.getLogger(__name__)


async def overs_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.OVERS_SELECT:
        await query.answer("Overs selection isn't active right now.", show_alert=True)
        return

    if user.id != match.creator_id:
        await query.answer("Only the Host can set the overs.", show_alert=True)
        return

    overs = int(query.data.split(":")[1])
    match.overs = overs
    match.status = MatchStatus.SPAM_FREE_DECISION
    await query.answer(f"{overs} over match!")

    # Remove the overs keyboard now that a selection has been made, so it
    # can't be tapped again.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:  # noqa: BLE001 - best-effort UI cleanup
        logger.debug("Could not clear overs keyboard: %s", exc)

    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"✅ *Match set for {overs} Overs per side!*",
        parse_mode=ParseMode.MARKDOWN,
    )

    host = match.team_a.players.get(match.creator_id) or match.team_b.players.get(match.creator_id)
    host_name = host.display_name if host else (match.host_display_name or "Host")

    await context.bot.send_message(
        chat_id=match.chat_id,
        text=(
            f"⚠️ {host_name}, you can make this game spam-free by clicking on /spamfree\n\n"
            f"You have {config.SPAM_FREE_DECISION_SECONDS} seconds to decide. After "
            f"{config.SPAM_FREE_DECISION_SECONDS} seconds if you do not /spamfree then spam is "
            "allowed and we proceed to the game!!"
        ),
    )

    job_name = f"spamfree_timeout_{match.chat_id}_{match.match_id}"
    match.spamfree_job_name = job_name
    context.job_queue.run_once(
        spamfree_timeout_callback,
        when=config.SPAM_FREE_DECISION_SECONDS,
        chat_id=match.chat_id,
        name=job_name,
        data={"chat_id": match.chat_id, "match_id": match.match_id},
    )


async def spamfree_timeout_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data = context.job.data
    store = get_store(context)
    match = store.get(job_data["chat_id"])

    if match is None or match.status != MatchStatus.SPAM_FREE_DECISION or match.match_id != job_data["match_id"]:
        return  # already decided, or match moved on

    match.spam_free_mode = False
    await start_lineup_phase(match, context)


async def changeover_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/changeover <1-20> -- Host-only, works at any point in a Team Match
    (lobby or mid-game) to change how many overs per side the match is
    set for."""
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active match or lobby in this chat.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text("Solo Match doesn't use a fixed overs count.")
        return

    if user.id != match.creator_id:
        await update.message.reply_text("Only the Host can change the overs.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /changeover <number>  (1-20)")
        return

    try:
        new_overs = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Overs must be a whole number, e.g. /changeover 5")
        return

    if not 1 <= new_overs <= 20:
        await update.message.reply_text("Overs must be between 1 and 20.")
        return

    if match.status is MatchStatus.IN_PROGRESS and new_overs <= match.current_over:
        await update.message.reply_text(
            f"Can't set it that low -- {match.current_over} over(s) have already been bowled "
            f"this innings. Pick something above {match.current_over}."
        )
        return

    match.overs = new_overs
    await update.message.reply_text(
        f"✅ Overs updated! The match is now set for {new_overs} overs per side."
    )


async def spamfree_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.SPAM_FREE_DECISION:
        await update.message.reply_text("There's no spam-free decision window open right now.")
        return

    if user.id != match.creator_id:
        await update.message.reply_text("Only the Host can activate spam-free mode.")
        return

    if match.spamfree_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.spamfree_job_name):
            job.schedule_removal()

    match.spam_free_mode = True
    await update.message.reply_text(
        "🛡️ *SPAM-FREE MODE ACTIVATED!* Bowlers cannot bowl the same delivery more than twice in a row.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await start_lineup_phase(match, context)


async def start_lineup_phase(match, context: ContextTypes.DEFAULT_TYPE) -> None:
    match.status = MatchStatus.AWAITING_LINEUP

    batting_captain = match.batting_team.players[match.batting_team.captain_id]
    bowling_captain = match.bowling_team.players[match.bowling_team.captain_id]

    from utils.scoreboard import escape_md

    text = (
        f"{escape_md(batting_captain.display_name)} ({escape_md(match.batting_team.name)}) or the Host, use "
        f"/batting to select your opening pair.\n"
        f"{escape_md(bowling_captain.display_name)} ({escape_md(match.bowling_team.name)}) or the Host, use "
        f"/bowling to select your first bowler."
    )
    await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
