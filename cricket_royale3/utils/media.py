"""
Safe media helpers.

If the operator hasn't dropped the real banner/GIF files into assets/ yet,
these helpers degrade gracefully to a plain text message instead of
crashing the bot with a "file not found" error. They also retry once on
a network timeout (large GIF/MP4 uploads can occasionally time out) and
fall back to plain text rather than letting the whole update crash.

Performance note: the first time a given file is sent, Telegram has to
receive the full upload from us, which is slow and the main reason large
GIFs/MP4s were timing out under load (e.g. on Railway). Every send after
that reuses the file_id Telegram gave back for that exact file, which is
sent to Telegram's servers directly and returns near-instantly -- no
re-upload of the bytes at all. This file_id is cached both in-memory (for
this process) and in the database's media_cache table, so a restart/
redeploy (very common on Railway) doesn't force every asset to slowly
re-upload again -- only genuinely new/changed assets ever pay that cost.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_file_id_cache: dict[str, str] = {}
_dimensions_cache: dict[str, Optional[tuple[int, int]]] = {}


def _cache_key(path: Path) -> str:
    return str(path.resolve())


def _probe_dimensions_sync(path: Path) -> Optional[tuple[int, int]]:
    """Reads (width, height) from the video file via ffprobe. Without
    this, Telegram has to guess the aspect ratio for an animation's group
    preview and sometimes gets it wrong (crops/squeezes a file that's
    actually fine) -- passing the real dimensions explicitly to
    send_animation avoids that guesswork entirely."""
    import subprocess
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0", str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("x")
        if len(parts) != 2:
            return None
        return int(parts[0]), int(parts[1])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        return None


async def _get_dimensions(path: Path) -> Optional[tuple[int, int]]:
    key = _cache_key(path)
    if key in _dimensions_cache:
        return _dimensions_cache[key]
    import asyncio
    dims = await asyncio.to_thread(_probe_dimensions_sync, path)
    _dimensions_cache[key] = dims
    return dims


async def send_photo_or_text(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photo_path: Path,
    caption: str,
    reply_markup=None,
) -> Message:
    if photo_path.exists():
        key = _cache_key(photo_path)
        db = context.application.bot_data.get("db")
        cached_id = _file_id_cache.get(key) or (await db.get_cached_file_id(key) if db else None)
        if cached_id:
            _file_id_cache[key] = cached_id

        if cached_id:
            try:
                return await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=cached_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                )
            except BadRequest as exc:
                # The cached file_id can go stale (e.g. Telegram evicted it
                # after ~ months of disuse) -- fall through and re-upload.
                logger.warning("Cached photo file_id stale for %s (%s); re-uploading.", photo_path, exc)
                _file_id_cache.pop(key, None)
                if db:
                    await db.clear_cached_file_id(key)

        for attempt in (1, 2):
            try:
                with open(photo_path, "rb") as f:
                    msg = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=reply_markup,
                    )
                if msg.photo:
                    file_id = msg.photo[-1].file_id
                    _file_id_cache[key] = file_id
                    if db:
                        await db.set_cached_file_id(key, file_id)
                return msg
            except TimedOut:
                logger.warning("send_photo timed out (attempt %d) for %s", attempt, photo_path)
        logger.warning("send_photo kept timing out for %s - falling back to text.", photo_path)
    else:
        logger.warning("Missing asset %s - falling back to text.", photo_path)
    return await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
    )


async def send_gif_or_text(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    gif_path: Path,
    caption: Optional[str] = None,
    reply_markup=None,
) -> Optional[Message]:
    if gif_path.exists():
        key = _cache_key(gif_path)
        db = context.application.bot_data.get("db")
        cached_id = _file_id_cache.get(key) or (await db.get_cached_file_id(key) if db else None)
        if cached_id:
            _file_id_cache[key] = cached_id

        dims = await _get_dimensions(gif_path)
        width, height = dims if dims else (None, None)

        if cached_id:
            try:
                return await context.bot.send_animation(
                    chat_id=chat_id,
                    animation=cached_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN if caption else None,
                    reply_markup=reply_markup,
                    width=width,
                    height=height,
                )
            except BadRequest as exc:
                logger.warning("Cached gif file_id stale for %s (%s); re-uploading.", gif_path, exc)
                _file_id_cache.pop(key, None)
                if db:
                    await db.clear_cached_file_id(key)

        for attempt in (1, 2):
            try:
                with open(gif_path, "rb") as f:
                    msg = await context.bot.send_animation(
                        chat_id=chat_id,
                        animation=f,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN if caption else None,
                        reply_markup=reply_markup,
                        width=width,
                        height=height,
                    )
                if msg.animation:
                    file_id = msg.animation.file_id
                    _file_id_cache[key] = file_id
                    if db:
                        await db.set_cached_file_id(key, file_id)
                return msg
            except TimedOut:
                logger.warning("send_animation timed out (attempt %d) for %s", attempt, gif_path)
        logger.warning("send_animation kept timing out for %s - falling back to text.", gif_path)
    else:
        logger.warning("Missing asset %s - skipping GIF.", gif_path)
    if caption:
        return await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )
    return None
