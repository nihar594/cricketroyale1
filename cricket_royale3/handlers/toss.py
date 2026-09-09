"""
Toss: send the toss GIF, let a randomly-chosen captain call Heads/Tails,
then decide the winner with a genuine 50/50 coin flip (independent of
what was called -- calling correctly isn't a mechanic here, it's just
flavour for who gets to make the call), and finally let the winning
captain choose to Bat or Bowl first.
"""

from __future__ import annotations

import logging
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.keyboards import toss_call_keyboard, toss_decision_keyboard
from utils.media import send_gif_or_text
from utils.models import MatchStatus, TeamKey

logger = logging.getLogger(__name__)


async def start_toss(match, context: ContextTypes.DEFAULT_TYPE) -> None:
    match.status = MatchStatus.TOSS

    caller_key = random.choice([TeamKey.A, TeamKey.B])
    match.toss_caller_key = caller_key
    caller_team = match.team(caller_key)
    caller_captain = caller_team.players[caller_team.captain_id]

    from utils.scoreboard import escape_md

    caption = (
        f"🪙✨ *TOSS TIME!* ✨🪙\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👉 {escape_md(caller_captain.display_name)} ({escape_md(caller_team.name)}), call it -- "
        f"HEADS or TAILS?"
    )
    await send_gif_or_text(
        context=context,
        chat_id=match.chat_id,
        gif_path=config.toss_gif(),
        caption=caption,
        reply_markup=toss_call_keyboard(),
    )


async def toss_call_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.TOSS:
        await query.answer("There's no toss happening right now.", show_alert=True)
        return

    caller_team = match.team(match.toss_caller_key)
    if user.id != caller_team.captain_id:
        await query.answer("Only the chosen captain can call the toss.", show_alert=True)
        return

    call = query.data.split(":")[2]  # "heads" or "tails"

    # A genuine 50/50 coin flip -- completely independent of what was
    # called, so every toss is a true 50-50 regardless of who calls what.
    winner_key = random.choice([TeamKey.A, TeamKey.B])
    match.toss_winner = winner_key
    winner_team = match.team(winner_key)
    winner_captain = winner_team.players[winner_team.captain_id]

    await query.answer(f"{call.title()} it is!")

    from utils.scoreboard import escape_md

    caption = (
        f"🪙 The coin landed... \n\n"
        f"🎉 *{escape_md(winner_team.name)}* (captain {escape_md(winner_captain.display_name)}) has won the toss!\n\n"
        f"👉 {escape_md(winner_captain.display_name)}, will you Bat or Bowl first?"
    )
    await context.bot.send_message(
        chat_id=match.chat_id,
        text=caption,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=toss_decision_keyboard(),
    )


async def toss_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.TOSS:
        await query.answer("There's no toss happening right now.", show_alert=True)
        return

    winner_team = match.team(match.toss_winner)
    if user.id != winner_team.captain_id:
        await query.answer("Only the toss-winning captain can make this call.", show_alert=True)
        return

    decision = query.data.split(":")[2]  # "bat" or "bowl"
    match.toss_decision = decision
    match.batting_team_key = match.toss_winner if decision == "bat" else match.toss_winner.other
    match.status = MatchStatus.OVERS_SELECT

    await query.answer(f"{winner_team.name} will {decision} first!")

    if decision == "bat":
        choice_text = "✅ The captain chose to bat 🏏 first!"
    else:
        choice_text = "✅ The captain chose to bowl 🥎 first!"
    await context.bot.send_message(chat_id=match.chat_id, text=choice_text)

    from utils.keyboards import overs_keyboard
    from utils.scoreboard import escape_md

    text = (
        f"🏏 Batting first: *{escape_md(match.batting_team.name)}*\n"
        f"🎯 Bowling first: *{escape_md(match.bowling_team.name)}*\n\n"
        "How many overs should this match be? (1-20)"
    )
    await context.bot.send_message(
        chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=overs_keyboard(),
    )
