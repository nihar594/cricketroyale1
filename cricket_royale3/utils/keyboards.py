"""
Inline keyboard builders for Cricket Royale.

Keeping all keyboard construction in one place makes callback_data
conventions easy to audit. Convention: "namespace:action:arg1:arg2".
"""

from __future__ import annotations

from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import MAX_OVERS, MIN_OVERS
from utils.models import MatchState, TeamKey


def start_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🏏 Solo Match", callback_data="start:solo"),
            InlineKeyboardButton("👥 Team Match", callback_data="start:team"),
        ],
        [
            InlineKeyboardButton("🏆 Leaderboard", callback_data="start:leaderboard"),
            InlineKeyboardButton("❌ Cancel", callback_data="start:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def host_select_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎪 Main Host Banunga", callback_data="host:claim")],
        [InlineKeyboardButton("❌ Cancel", callback_data="host:cancel")],
    ]
    return InlineKeyboardMarkup(rows)


def create_team_button_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📋 Open Team Registration", callback_data="lobby:createteam")]]
    return InlineKeyboardMarkup(rows)


def lobby_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🅰️ Join Team A", callback_data="lobby:join:A"),
            InlineKeyboardButton("🅱️ Join Team B", callback_data="lobby:join:B"),
        ],
        [
            InlineKeyboardButton("🚪 Leave Lobby", callback_data="lobby:leave"),
            InlineKeyboardButton("⚡ Force Start", callback_data="lobby:forcestart"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def captain_select_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🅰️ Become Team A Captain", callback_data="captain:A")],
        [InlineKeyboardButton("🅱️ Become Team B Captain", callback_data="captain:B")],
    ]
    return InlineKeyboardMarkup(rows)


def toss_call_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("HEADS", callback_data="toss:call:heads"),
            InlineKeyboardButton("TAILS", callback_data="toss:call:tails"),
        ]
    ]
    return InlineKeyboardMarkup(rows)


def toss_decision_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🏏 Bat First", callback_data="toss:decide:bat"),
            InlineKeyboardButton("🎯 Bowl First", callback_data="toss:decide:bowl"),
        ]
    ]
    return InlineKeyboardMarkup(rows)


def overs_keyboard() -> InlineKeyboardMarkup:
    """A grid of buttons for every legal overs value (1-20)."""
    numbers = list(range(MIN_OVERS, MAX_OVERS + 1))
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for n in numbers:
        row.append(InlineKeyboardButton(str(n), callback_data=f"overs:{n}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def end_confirm_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✅ Confirm End", callback_data="endmatch:confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="endmatch:cancel"),
        ]
    ]
    return InlineKeyboardMarkup(rows)


def bowl_now_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start=bowl"
    rows = [[InlineKeyboardButton("🎯 Bowl Now (Opens DM)", url=url)]]
    return InlineKeyboardMarkup(rows)


def back_to_game_keyboard(chat_username: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/{chat_username}"
    rows = [[InlineKeyboardButton("⬅ Back to Game", url=url)]]
    return InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------
# Leaderboard / Hall of Fame
# --------------------------------------------------------------------------
LEADERBOARD_CATEGORIES = [
    ("ducks", "🦆 Duck Ranking"),
    ("sixes", "💥 Sixes Ranking"),
    ("wickets", "🥎 Wickets Ranking"),
    ("runs", "🏃 Total Runs"),
    ("fifties", "🌟 Half-Centuries"),
    ("centuries", "💯 Centuries"),
    ("highest_score", "🏆 Most Runs in Match"),
]


def leaderboard_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    labels = LEADERBOARD_CATEGORIES
    for i in range(0, len(labels), 2):
        pair = labels[i:i + 2]
        rows.append([InlineKeyboardButton(label, callback_data=f"lb:cat:{key}:0") for key, label in pair])
    rows.append([InlineKeyboardButton("💎 Gems Ranking", callback_data="lb:cat:gems:0")])
    return InlineKeyboardMarkup(rows)


def leaderboard_page_keyboard(category: str, offset: int, has_next: bool) -> InlineKeyboardMarkup:
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton("⬅ Back", callback_data=f"lb:cat:{category}:{max(0, offset - 10)}"))
    if has_next:
        nav_row.append(InlineKeyboardButton("➡ Next", callback_data=f"lb:cat:{category}:{offset + 10}"))
    rows = []
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton("🏠 Return to Ranking menu", callback_data="lb:menu")])
    return InlineKeyboardMarkup(rows)
