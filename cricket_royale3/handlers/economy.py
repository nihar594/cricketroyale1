"""
Gems economy: /gems balance, /give transfers, /flip coin-flip gambling,
plus the match-reward message sent right after Player of the Match at
the end of every match.
"""

from __future__ import annotations

import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from utils.scoreboard import escape_md


async def gems_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)
    gems = await db.get_gems(user.id)

    display_name = f"@{user.username}" if user.username else escape_md(user.first_name)
    text = (
        "💎 *Your Balance*\n\n"
        f"👤 User: {display_name}\n"
        f"🆔 ID: {user.id}\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 *Total Gems:* {gems}\n"
        "━━━━━━━━━━━━━━━\n\n"
        "🔥 Keep playing matches to earn more gems!\n"
        "🏆 Win POTM & boost your rewards!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Gems to another player. Either reply to their message:
        /give 5000
    or tag them directly:
        /give 5000 @username
    (tagging only works if that person has used the bot before, since
    Telegram doesn't resolve @usernames to an ID for us)."""
    sender = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(sender.id, sender.username, sender.first_name)

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "• Reply to their message: /give 5000\n"
            "• Or tag them: /give 5000 @username"
        )
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /give 500 @username")
        return

    if amount <= 0:
        await update.message.reply_text("Amount must be greater than 0.")
        return

    recipient_id = None
    recipient_username = None
    recipient_first_name = None
    recipient_name = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
        recipient_id = target.id
        recipient_username = target.username
        recipient_first_name = target.first_name
        recipient_name = f"@{target.username}" if target.username else escape_md(target.first_name)
    elif len(args) >= 2:
        username_arg = args[1]
        row = await db.get_user_by_username(username_arg)
        if row is None:
            await update.message.reply_text(
                f"Couldn't find {username_arg} -- they need to have used the bot at least once "
                "(e.g. /start) before you can send them Gems this way. You can also reply "
                "directly to their message with /give <amount> instead."
            )
            return
        recipient_id = row["user_id"]
        recipient_username = row["username"]
        recipient_first_name = row["first_name"]
        recipient_name = f"@{row['username']}" if row["username"] else escape_md(row["first_name"])
    else:
        await update.message.reply_text(
            "Tell me who to send Gems to -- reply to their message with /give <amount>, "
            "or tag them: /give <amount> @username"
        )
        return

    if recipient_id == sender.id:
        await update.message.reply_text("You can't send Gems to yourself!")
        return

    sender_gems = await db.get_gems(sender.id)
    if sender_gems < amount and sender.id != config.OWNER_ID:
        await update.message.reply_text(f"You don't have enough Gems for that. Your balance: {sender_gems} 💎")
        return

    await db.add_gems(sender.id, -amount, sender.username, sender.first_name)
    await db.add_gems(recipient_id, amount, recipient_username, recipient_first_name)

    sender_name = f"@{sender.username}" if sender.username else escape_md(sender.first_name)
    text = (
        "🎁 *Gems Sent!*\n\n"
        f"👤 From: {sender_name}\n"
        f"👤 To: {recipient_name}\n"
        f"💰 Amount: {amount} 💎\n\n"
        "✅ Transfer complete!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def flip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Coin-flip gambling: /flip h <amount> or /flip t <amount>.
    The house keeps a slight edge -- config.FLIP_WIN_CHANCE (45%) chance
    to win, 55% to lose."""
    user = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /flip <h|t> <amount>\nExample: /flip h 200  (h = heads, t = tails)"
        )
        return

    choice_raw = args[0].lower()
    if choice_raw in ("h", "heads"):
        user_choice = "heads"
    elif choice_raw in ("t", "tails"):
        user_choice = "tails"
    else:
        await update.message.reply_text("Choose h (heads) or t (tails). Example: /flip h 200")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /flip h 200")
        return

    if amount <= 0:
        await update.message.reply_text("Bet amount must be greater than 0.")
        return

    balance = await db.get_gems(user.id)
    if balance < amount:
        await update.message.reply_text(f"You don't have enough Gems for that bet. Your balance: {balance} 💎")
        return

    user_wins = random.random() < config.FLIP_WIN_CHANCE
    result = user_choice if user_wins else ("tails" if user_choice == "heads" else "heads")

    if user_wins:
        await db.add_gems(user.id, amount, user.username, user.first_name)
        new_balance = balance + amount
        text = (
            f"🎲 Coin Flipped: {result.upper()} 🪙\n"
            "✅ *You Won!*\n\n"
            f"💎 Bet: {amount}\n"
            f"🏆 Won: +{amount}\n"
            f"💰 Total Gems: {new_balance}"
        )
    else:
        await db.add_gems(user.id, -amount, user.username, user.first_name)
        new_balance = balance - amount
        text = (
            f"🎲 Coin Flipped: {result.upper()} 🪙\n"
            "❌ *You Lost!*\n\n"
            f"💎 Bet: {amount}\n"
            f"💸 Lost: -{amount}\n"
            f"💰 Total Gems: {new_balance}"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dice gambling: /dice <amount>. Rolls 1-6 -- 4/5/6 is a win, 1/2/3
    is a loss. Overall win probability is config.DICE_WIN_CHANCE (45%),
    same house edge as /flip; which face shows is picked to match
    whichever outcome was already decided."""
    user = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /dice <amount>\nExample: /dice 200")
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /dice 200")
        return

    if amount <= 0:
        await update.message.reply_text("Bet amount must be greater than 0.")
        return

    balance = await db.get_gems(user.id)
    if balance < amount:
        await update.message.reply_text(f"You don't have enough Gems for that bet. Your balance: {balance} 💎")
        return

    user_wins = random.random() < config.DICE_WIN_CHANCE
    roll = random.choice([4, 5, 6]) if user_wins else random.choice([1, 2, 3])

    if user_wins:
        await db.add_gems(user.id, amount, user.username, user.first_name)
        text = f"🎲 Dice Rolled: {roll}\n🏆 *You Won!*\n💎 +{amount} Gems"
    else:
        await db.add_gems(user.id, -amount, user.username, user.first_name)
        text = f"🎲 Dice Rolled: {roll}\n❌ *You Lost!*\n💎 -{amount} Gems"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def send_match_rewards(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    winner_name: str,
    potm_name: str,
    winner_coins: int,
    potm_coins: int,
) -> None:
    """Sent as its own message right after the Player of the Match
    announcement -- does not touch/replace that message."""
    text = (
        "🏁 *Match Completed!*\n\n"
        f"🏆 *Winner:* {winner_name}\n"
        f"🎯 *Player of the Match:* {potm_name}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "💰 *Match Rewards*\n\n"
        f"🥇 Winner Reward: +{winner_coins} Coins\n"
        f"🌟 POTM Bonus: +{potm_coins} Coins\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "💎 Keep playing to earn more rewards!\n"
        "🔥 Next match awaits… are you ready?"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
