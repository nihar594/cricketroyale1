"""
/batting - list available batters with a number next to each, then pick
one with /batting [number]. Usable by the batting captain or the Host.
Run it twice in a row to set both the striker and non-striker.
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.models import MatchMode, MatchStatus
from utils.scoreboard import escape_md


def _batter_status(match, player) -> str:
    if player.is_out:
        return "❌ (Out)"
    if player.user_id == match.striker_id or player.user_id == match.non_striker_id:
        return "🏏 (On Pitch)"
    return "✅ (Available)"


async def _send_batter_list(update: Update, match) -> None:
    roster = list(match.batting_team.players.values())
    lines = ["🏏 *AVAILABLE BATTERS:*"]
    for i, p in enumerate(roster, start=1):
        lines.append(f"[{i}] {escape_md(p.display_name)} - {_batter_status(match, p)}")
    lines.append("")
    lines.append("👉 Usage: /batting [number] to select.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def batting_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status not in (MatchStatus.AWAITING_LINEUP, MatchStatus.IN_PROGRESS):
        await update.message.reply_text("There's no match awaiting a batting lineup right now.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text(
            "Solo Match (Royale mode) sets the batting order automatically by join order -- /batting isn't needed."
        )
        return

    batting_team = match.batting_team
    if user.id != batting_team.captain_id and user.id != match.creator_id:
        await update.message.reply_text(
            f"Only the {batting_team.name} captain or the Host can select batters."
        )
        return

    if match.striker_id and match.non_striker_id:
        await update.message.reply_text("Both batters are already at the crease.")
        return

    if not context.args:
        await _send_batter_list(update, match)
        return

    roster = list(batting_team.players.values())
    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /batting [number] -- run /batting with no number to see the list.")
        return

    if idx < 1 or idx > len(roster):
        await update.message.reply_text("That number isn't on the list. Run /batting to see valid numbers.")
        return

    player = roster[idx - 1]
    if player.is_out:
        await update.message.reply_text(f"{player.display_name} is already out.")
        return
    if player.user_id == match.striker_id or player.user_id == match.non_striker_id:
        await update.message.reply_text(f"{player.display_name} is already on the pitch.")
        return

    if match.striker_id is None:
        match.striker_id = player.user_id
        await update.message.reply_text(f"🔸 Striker: {player.display_name}")

        if config.OWNER_ID and player.user_id == config.OWNER_ID:
            from utils.media import send_gif_or_text
            from utils.scoreboard import format_batter_arrival
            await send_gif_or_text(
                context=context, chat_id=match.chat_id, gif_path=config.batting_gif(),
                caption=format_batter_arrival(player.display_name),
            )
    elif match.non_striker_id is None and batting_team.size > 1:
        match.non_striker_id = player.user_id

        from utils.media import send_gif_or_text

        from utils.scoreboard import escape_md

        bowling_team = match.bowling_team
        bowling_captain = bowling_team.players.get(bowling_team.captain_id)
        host = match.batting_team.players.get(match.creator_id) or match.bowling_team.players.get(match.creator_id)
        host_name = escape_md(host.display_name) if host else (match.host_display_name or "Host")

        caption_lines = [f"🏏 {escape_md(player.display_name)} selected as Non-Striker!", ""]
        if bowling_captain:
            caption_lines.append(
                f"Bowling Team Captain ({escape_md(bowling_captain.display_name)}) / Host {host_name}, "
                f"type /bowling to see bowlers or /bowling [num] to select opening bowler."
            )
        else:
            caption_lines.append(
                f"Bowling Team Captain/Host, type /bowling to see bowlers or /bowling [num] "
                f"to select opening bowler."
            )

        await send_gif_or_text(
            context=context,
            chat_id=match.chat_id,
            gif_path=config.non_striker_gif(),
            caption="\n".join(caption_lines),
        )
    else:
        await update.message.reply_text("Both batters are already at the crease.")
        return

    from handlers.gameplay import maybe_ready_to_bowl
    await maybe_ready_to_bowl(match, context)
