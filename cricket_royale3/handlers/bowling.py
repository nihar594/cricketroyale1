"""
/bowling - list available bowlers with a number next to each, then pick
one with /bowling [number]. Usable by the bowling captain or the Host.
The bowler who just finished an over cannot be reselected for the
following over (standard cricket rule).
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.start import get_store
from utils.models import MatchMode, MatchStatus
from utils.scoreboard import escape_md


def _overs_display(player) -> str:
    overs = player.balls_bowled // 6
    balls = player.balls_bowled % 6
    return f"{overs}.{balls} Ov"


def _bowler_status(match, player) -> str:
    if player.user_id == match.last_bowler_id:
        return "🚫 (Just Bowled)"
    return "✅ (Available)"


async def _send_bowler_list(update: Update, match) -> None:
    roster = list(match.bowling_team.players.values())
    lines = ["🥎 *AVAILABLE BOWLERS:*"]
    for i, p in enumerate(roster, start=1):
        lines.append(f"[{i}] {escape_md(p.display_name)} - {_overs_display(p)} - {_bowler_status(match, p)}")
    lines.append("")
    lines.append("👉 Usage: /bowling [number] to select.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def bowling_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status not in (MatchStatus.AWAITING_LINEUP, MatchStatus.IN_PROGRESS):
        await update.message.reply_text("There's no match awaiting a bowler right now.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text(
            "Solo Match (Royale mode) rotates the bowler automatically -- /bowling isn't needed."
        )
        return

    bowling_team = match.bowling_team
    if user.id != bowling_team.captain_id and user.id != match.creator_id:
        await update.message.reply_text(f"Only the {bowling_team.name} captain or the Host can pick the bowler.")
        return

    if match.bowler_id is not None:
        await update.message.reply_text("A bowler is already set for this over.")
        return

    if not context.args:
        await _send_bowler_list(update, match)
        return

    roster = list(bowling_team.players.values())
    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /bowling [number] -- run /bowling with no number to see the list.")
        return

    if idx < 1 or idx > len(roster):
        await update.message.reply_text("That number isn't on the list. Run /bowling to see valid numbers.")
        return

    player = roster[idx - 1]
    if player.user_id == match.last_bowler_id:
        await update.message.reply_text(f"{player.display_name} just bowled the previous over and can't bowl this one.")
        return

    match.bowler_id = player.user_id
    # A new bowler starts with a clean slate for the spam-free 3-in-a-row check.
    match.recent_bowler_numbers = []
    await update.message.reply_text(f"🎯 Bowler: {player.display_name}")

    from handlers.gameplay import maybe_ready_to_bowl
    await maybe_ready_to_bowl(match, context)
