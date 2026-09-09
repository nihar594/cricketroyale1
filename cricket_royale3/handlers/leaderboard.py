"""
/leaderboard - Hall of Fame menu: pick a category (Duck Ranking, Sixes,
Wickets, Total Runs, Half-Centuries, Centuries, Most Runs in a Match,
Gems Ranking) and page through the Top 10 (Top 20 for Gems), with your
own rank shown even if you're outside the visible page.

/userstats - visual career stats card with the player's profile photo.
"""

from __future__ import annotations

import io

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from utils.keyboards import LEADERBOARD_CATEGORIES, leaderboard_menu_keyboard, leaderboard_page_keyboard
from utils.scoreboard import escape_md

MENU_TEXT = (
    "🏆 *WELCOME TO THE HALL OF FAME!* 🏆\n\n"
    "This is where legends are remembered.\n"
    "The greatest performers in our arena live here forever.\n\n"
    "🌟 Select a category to see the Top 10 (Top 20 for Gems):"
)

_CATEGORY_TITLES = {
    "ducks": "🦆🦆 DUCK RANKING",
    "sixes": "💥💥 SIXES RANKING",
    "wickets": "🥎🥎 WICKETS RANKING",
    "runs": "🏃🏃 TOTAL RUNS",
    "fifties": "🌟🌟 HALF-CENTURIES",
    "centuries": "💯💯 CENTURIES",
    "highest_score": "🏆🏆 MOST RUNS IN A MATCH",
    "gems": "💎💎 GEMS RANKING",
}

_CATEGORY_UNIT = {
    "ducks": "ducks",
    "sixes": "sixes",
    "wickets": "wickets",
    "runs": "runs",
    "fifties": "fifties",
    "centuries": "centuries",
    "highest_score": None,  # formatted specially, "N runs (M balls)"
    "gems": "💎",
}

PAGE_SIZE = 10


def _name(row) -> str:
    if row["username"]:
        return f"@{row['username']}"
    return row["first_name"] or "Player"


def _value_text(category: str, row) -> str:
    if category == "highest_score":
        return f"{row['value']} runs ({row['balls_faced']} balls)"
    unit = _CATEGORY_UNIT[category]
    if category == "gems":
        return f"{row['value']} 💎"
    return f"{row['value']} {unit}"


async def render_leaderboard_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Legacy plain-text overview, kept for anywhere still calling it
    directly (e.g. a future /leaderboard summary outside the menu)."""
    db = context.application.bot_data["db"]
    rows = await db.get_leaderboard(limit=10)

    if not rows:
        return "🏆 *Leaderboard*\n━━━━━━━━━━━━━━━━━━\n\nNo matches played yet. Be the first champion! ✨"

    lines = ["🏆✨ *CRICKET ROYALE LEADERBOARD* ✨🏆", "━━━━━━━━━━━━━━━━━━", ""]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        name = f"@{row['username']}" if row["username"] else row["first_name"]
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(
            f"{prefix} {name} — {row['matches_won']}W / {row['matches_played']}P  "
            f"| {row['runs_scored']} runs | {row['wickets_taken']} wkts"
        )
    return "\n".join(lines)


async def render_category_page(context: ContextTypes.DEFAULT_TYPE, category: str, offset: int, user_id: int):
    """Returns (text, keyboard) for one page of a ranking category."""
    db = context.application.bot_data["db"]
    page_size = 10

    total = await db.get_total_ranked_players(category)
    rows = await db.get_ranking(category, limit=page_size, offset=offset)
    has_next = offset + page_size < total

    title = _CATEGORY_TITLES[category]
    lines = [f"{title} (Total Players: {total})", ""]

    my_rank = await db.get_rank_of(category, user_id)
    me_row = await db.get_user_stats(user_id)
    if my_rank and me_row:
        me_name = f"@{me_row['username']}" if me_row["username"] else me_row["first_name"]
        me_value_row = {"value": my_rank["value"], "balls_faced": my_rank["balls_faced"]}
        lines.append(f"👤 Your Rank: #{my_rank['rank']} — {escape_md(me_name)}")
        lines.append(f"🦅 — {_value_text(category, me_value_row)}")
        lines.append("✦ ─────────── ✦")
        lines.append("")

    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        rank_num = offset + i + 1
        prefix = medals[i] if offset == 0 and i < 3 else f"#{rank_num}"
        name = escape_md(_name(row))
        lines.append(f"{prefix} {name} — *{_value_text(category, row)}*")

    if not rows:
        lines.append("_No one has any stats in this category yet -- keep playing!_")

    lines.append("")
    lines.append("✦ ─────────── ✦")
    lines.append("")
    lines.append("🏏 _Ties broken randomly — keep playing!_")

    keyboard = leaderboard_page_keyboard(category, offset, has_next)
    return "\n".join(lines), keyboard


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=leaderboard_menu_keyboard()
    )


LEADERBOARD_CATEGORIES_LOOKUP = {key for key, _ in LEADERBOARD_CATEGORIES} | {"gems"}


async def leaderboard_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles both the /leaderboard menu button (start:leaderboard) and
    all lb:* callbacks: lb:menu (return to the category grid) and
    lb:cat:<category>:<offset> (a paginated ranking page)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    async def show_menu():
        if query.message.photo or query.message.caption is not None:
            await query.message.reply_text(
                MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=leaderboard_menu_keyboard()
            )
        else:
            await query.edit_message_text(
                MENU_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=leaderboard_menu_keyboard()
            )

    if data == "start:leaderboard":
        await show_menu()
        return

    if data == "lb:menu":
        await show_menu()
        return

    # lb:cat:<category>:<offset>
    _, _, category, offset_str = data.split(":")
    offset = int(offset_str)

    if category not in LEADERBOARD_CATEGORIES_LOOKUP:
        await query.answer("Unknown category.", show_alert=True)
        return

    text, keyboard = await render_category_page(context, category, offset, query.from_user.id)
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    except Exception:  # noqa: BLE001 - edit can fail (e.g. identical content); send fresh instead
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


async def userstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows a full career stats card -- the player's Telegram profile
    photo composited into the Cricket Royale userstats banner, followed
    by the full detailed text breakdown. Reply to someone's message with
    /userstats to check their stats instead of your own."""
    target_user = update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user

    db = context.application.bot_data["db"]
    row = await db.get_user_stats(target_user.id)

    if row is None or row["matches_played"] == 0:
        who = "You haven't" if target_user.id == update.effective_user.id else f"{target_user.first_name} hasn't"
        await update.message.reply_text(f"{who} played any matches yet. Jump into a Solo or Team Match first!")
        return

    display_name = f"@{target_user.username}" if target_user.username else target_user.first_name
    gems = await db.get_gems(target_user.id)

    from utils.userstats_card import build_userstats_card
    image_bytes = await build_userstats_card(context, target_user.id, row)

    if image_bytes:
        await update.message.reply_photo(
            photo=io.BytesIO(image_bytes),
            caption=f"📊 *{display_name}'s Career Stats* — @cricketroyale #cricketroyale",
            parse_mode=ParseMode.MARKDOWN,
        )

    from utils.userstats import format_user_stats
    text = format_user_stats(row, display_name, gems)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
