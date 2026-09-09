"""
Player of the Match: at the end of every match, fetches the standout
player's Telegram profile photo and composites it into the circular
frame on PLAYER_OF_MATCH_BANNER, then sends the result along with a
randomized commentary line about their standout performance.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
from typing import List, Optional

from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from utils.models import Player

logger = logging.getLogger(__name__)

BATTING_HIGHLIGHT_LINES = [
    "A match-defining knock! {name} tore the attack apart with {runs} off {balls} balls.",
    "Sheer domination at the crease! {name} smashed {runs} runs at a blistering strike rate of {sr}.",
    "Electric batting! {name} took the game away from the opposition with {runs} clinical runs.",
    "Class apart! {name}'s {runs}-run masterclass single-handedly swung the match.",
    "Unstoppable! {name} launched an assault for {runs} runs and never let go of the momentum.",
    "An absolute batting exhibition -- {name} finished unbeaten on {runs} off {balls}, no answer from the bowlers.",
]

BOWLING_HIGHLIGHT_LINES = [
    "A bowling masterclass! {name} ripped through the order, picking up {wickets} wicket(s) for just {runs_conceded} runs.",
    "Lethal spell! {name} wrecked the batting lineup with {wickets} crucial wicket(s).",
    "Ice in the veins! {name} delivered under pressure, snaring {wickets} wicket(s) at a stingy economy.",
    "Hammer blow after hammer blow! {name} dismantled the opposition with {wickets} wicket(s).",
    "A vintage spell of bowling -- {name} took {wickets} wicket(s) and never let the batters settle.",
]

ALL_ROUND_LINES = [
    "A complete all-round performance from {name} -- {runs} runs and {wickets} wicket(s) that turned the match on its head.",
    "The ultimate difference-maker! {name} contributed everywhere -- bat and ball -- to seal this one.",
    "{name} did it all today -- {runs} runs with the bat and {wickets} wicket(s) with the ball. A complete performance.",
]


def _generate_commentary(player: Player) -> str:
    sr = round((player.runs / player.balls_faced) * 100, 1) if player.balls_faced else 0.0
    has_batting = player.balls_faced > 0
    has_bowling = player.wickets_taken > 0

    if has_batting and has_bowling:
        template = random.choice(ALL_ROUND_LINES)
    elif has_bowling:
        template = random.choice(BOWLING_HIGHLIGHT_LINES)
    else:
        template = random.choice(BATTING_HIGHLIGHT_LINES)

    return template.format(
        name=player.display_name,
        runs=player.runs,
        balls=player.balls_faced,
        sr=sr,
        wickets=player.wickets_taken,
        runs_conceded=player.runs_conceded,
    )


def pick_player_of_the_match(players: List[Player]) -> Optional[Player]:
    """Ranks by runs scored first, wickets taken as the tiebreaker -- a
    simple, defensible 'best overall performance' metric."""
    eligible = [p for p in players if p.balls_faced > 0 or p.balls_bowled > 0]
    if not eligible:
        return None
    return max(eligible, key=lambda p: (p.runs, p.wickets_taken))


def _composite_sync(photo_bytes: bytes) -> Optional[bytes]:
    """CPU-bound image work -- run this off the event loop via
    asyncio.to_thread. Returns PNG bytes, or None if the template asset
    is missing."""
    if not config.player_of_match_banner().exists():
        logger.warning("Missing asset %s - skipping Player of the Match image.", config.player_of_match_banner())
        return None

    from PIL import Image, ImageDraw

    template = Image.open(config.player_of_match_banner()).convert("RGBA")
    profile = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")

    radius = config.POM_CIRCLE_RADIUS
    diameter = radius * 2
    cx, cy = config.POM_CIRCLE_CENTER

    # Center-crop the profile photo to a square, then resize to fill the circle.
    w, h = profile.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    profile = profile.crop((left, top, left + side, top + side)).resize(
        (diameter, diameter), Image.LANCZOS
    )

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)

    template.paste(profile, (cx - radius, cy - radius), mask)

    buf = io.BytesIO()
    template.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


async def build_player_of_the_match_image(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> Optional[bytes]:
    """Downloads the player's current Telegram profile photo and returns
    the composited award image as PNG bytes, or None if they have no
    profile photo set or the template asset is missing."""
    try:
        photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
    except TelegramError as exc:
        logger.debug("Could not fetch profile photos for %s: %s", user_id, exc)
        return None

    if photos.total_count == 0 or not photos.photos:
        return None

    largest = photos.photos[0][-1]
    try:
        file = await context.bot.get_file(largest.file_id)
        raw = await file.download_as_bytearray()
    except TelegramError as exc:
        logger.debug("Could not download profile photo for %s: %s", user_id, exc)
        return None

    try:
        return await asyncio.to_thread(_composite_sync, bytes(raw))
    except Exception as exc:  # noqa: BLE001 - image processing can fail in many ways
        logger.warning("Player of the Match compositing failed: %s", exc)
        return None


async def send_player_of_the_match(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, player: Player
) -> None:
    """Best-effort send -- if the photo or template isn't available for
    any reason, this quietly does nothing rather than breaking the
    end-of-match flow."""
    image_bytes = await build_player_of_the_match_image(context, player.user_id)

    stats_line = f"📊 {player.runs} runs ({player.balls_faced}b)"
    if player.wickets_taken > 0 or player.balls_bowled > 0:
        stats_line += f" | {player.wickets_taken} wkts ({player.runs_conceded} runs conceded)"

    caption = (
        f"🏆 *Player of the Match:* {player.display_name}\n\n"
        f"{_generate_commentary(player)}\n\n"
        f"{stats_line}"
    )

    if image_bytes is None:
        # Fall back to just the plain template (no photo) if we have it,
        # otherwise skip entirely rather than sending a broken message.
        if config.player_of_match_banner().exists():
            with open(config.player_of_match_banner(), "rb") as f:
                await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption, parse_mode="Markdown")
        return

    await context.bot.send_photo(
        chat_id=chat_id, photo=io.BytesIO(image_bytes), caption=caption, parse_mode="Markdown"
    )
