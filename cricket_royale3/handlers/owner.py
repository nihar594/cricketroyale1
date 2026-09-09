"""
Owner-only tools. Every command here checks config.OWNER_ID and silently
ignores anyone else -- these commands don't even show up as "denied" to
regular users, they just do nothing.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from handlers.start import get_store

logger = logging.getLogger(__name__)


def is_owner(user_id: int) -> bool:
    return config.OWNER_ID != 0 and user_id == config.OWNER_ID


def _is_from_owner(update: Update) -> bool:
    user = update.effective_user
    return user is not None and is_owner(user.id)


async def ownerhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    text = (
        "👑 *Owner Commands*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🗣 /broadcast `<message>` — send an announcement to every chat "
        "the bot has ever seen (every group + every user who's DM'd it)\n"
        "   Reply to a photo with /broadcast `[caption]` to send an image announcement\n\n"
        "📊 /botstats — global bot usage stats\n\n"
        "🔧 /maintenance `on|off` — block new matches for everyone but you\n\n"
        "🛑 /forceend `<chat_id>` — force-end the match/lobby in any chat\n\n"
        "🚫 /banuser — reply to someone's message to ban them\n\n"
        "✅ /unbanuser — reply to someone's message to unban them\n\n"
        "📋 /banlist — see everyone currently banned\n\n"
        "👑 /ownerhelp — this menu"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast <message>           -- text-only announcement
    Reply to a photo with /broadcast [caption]  -- image announcement,
    sent with the same photo to every chat the bot has ever seen (every
    group it's in, and every user who has ever DM'd /start to it)."""
    if not _is_from_owner(update):
        return

    photo = None
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1].file_id
    elif update.message.photo:
        photo = update.message.photo[-1].file_id

    message = " ".join(context.args) if context.args else (update.message.caption or "")

    if not photo and not message:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n"
            "Or reply to a photo with /broadcast [caption] to broadcast an image."
        )
        return

    db = context.application.bot_data["db"]
    chat_ids = await db.get_all_chat_ids()

    caption = f"📢 *Announcement from Cricket Royale*\n━━━━━━━━━━━━━━━━━━\n\n{message}" if message else None

    status = await update.message.reply_text(f"📡 Broadcasting to {len(chat_ids)} chats...")
    sent, failed = 0, 0
    for chat_id in chat_ids:
        try:
            if photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN if caption else None,
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN,
                )
            sent += 1
        except TelegramError as exc:
            logger.debug("Broadcast failed for chat %s: %s", chat_id, exc)
            failed += 1
        await asyncio.sleep(0.05)  # gentle rate limiting so Telegram doesn't throttle us

    await status.edit_text(f"✅ Broadcast complete: {sent} sent, {failed} failed.")


async def botstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    db = context.application.bot_data["db"]
    stats = await db.get_bot_stats()
    store = get_store(context)
    active = len(store.all())
    maintenance = context.application.bot_data.get("maintenance", False)

    text = (
        "👑 *Bot-wide Stats*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Registered users: *{stats['total_users']}*\n"
        f"💬 Known chats: *{stats['total_chats']}*\n"
        f"🏏 Matches logged: *{stats['total_matches']}*\n"
        f"🔴 Active matches right now: *{active}*\n"
        f"🏃 Total career runs scored: *{stats['total_runs']}*\n"
        f"🎯 Total career wickets taken: *{stats['total_wickets']}*\n"
        f"🔧 Maintenance mode: *{'ON' if maintenance else 'OFF'}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = context.application.bot_data.get("maintenance", False)
        await update.message.reply_text(
            f"Maintenance mode is currently *{'ON' if current else 'OFF'}*.\nUsage: /maintenance on|off",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    on = context.args[0].lower() == "on"
    context.application.bot_data["maintenance"] = on
    await update.message.reply_text(
        f"🔧 Maintenance mode is now *{'ON' if on else 'OFF'}*.", parse_mode=ParseMode.MARKDOWN
    )


async def forceend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /forceend <chat_id>")
        return
    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("chat_id must be a number.")
        return

    store = get_store(context)
    match = store.get(target_chat_id)
    if match is None:
        await update.message.reply_text("No active match/lobby in that chat.")
        return

    if match.lobby_job_name:
        for job in context.application.job_queue.get_jobs_by_name(match.lobby_job_name):
            job.schedule_removal()

    store.remove(target_chat_id)
    try:
        await context.bot.send_message(chat_id=target_chat_id, text="🛑 This match was ended by the bot owner.")
    except TelegramError:
        pass
    await update.message.reply_text(f"✅ Force-ended the match in chat {target_chat_id}.")


async def banuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await update.message.reply_text("Reply to the person's message with /banuser to ban them.")
        return

    target = update.message.reply_to_message.from_user
    if is_owner(target.id):
        await update.message.reply_text("You can't ban yourself!")
        return

    db = context.application.bot_data["db"]
    await db.ban_user(target.id, target.username, target.first_name)

    name = f"@{target.username}" if target.username else target.first_name
    await update.message.reply_text(
        f"🚫 *{name}* has been banned from Cricket Royale.\n"
        "They can no longer /start, join Solo/Team matches, or use bot commands.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def unbanuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await update.message.reply_text("Reply to the person's message with /unbanuser to unban them.")
        return

    target = update.message.reply_to_message.from_user
    db = context.application.bot_data["db"]
    await db.unban_user(target.id)

    name = f"@{target.username}" if target.username else target.first_name
    await update.message.reply_text(
        f"✅ *{name}* has been unbanned and can use Cricket Royale again.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_from_owner(update):
        return
    db = context.application.bot_data["db"]
    rows = await db.get_banned_users()

    if not rows:
        await update.message.reply_text("🚫 *Ban List*\n━━━━━━━━━━━━━━━━━━\n\nNo one is banned right now.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["🚫 *Ban List*", "━━━━━━━━━━━━━━━━━━", ""]
    for i, row in enumerate(rows, start=1):
        name = f"@{row['username']}" if row["username"] else row["first_name"]
        lines.append(f"{i}. {name} (ID: {row['user_id']})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def is_banned(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Shared check used by /start, Team Match joining, and Solo Match
    /join to block banned users from playing."""
    db = context.application.bot_data.get("db")
    if db is None:
        return False
    return await db.is_user_banned(user_id)


def is_maintenance_mode(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get("maintenance", False))
