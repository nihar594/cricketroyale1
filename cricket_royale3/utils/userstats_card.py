"""
Builds the visual /userstats card: composites the player's Telegram
profile photo into the circular frame on config.userstats_template(),
and overlays their live career numbers into the template's stat boxes.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from utils.userstats import compute_exp, level_progress

logger = logging.getLogger(__name__)

# Right-aligned x position of the value text in each column, and the
# vertical center of each of the three stat rows. Measured against the
# original 1264x842 template -- if you swap in a differently laid-out
# template, re-measure these with a grid overlay and update.
COL_X = {
    "batting": 180,
    "bowling": 335,
    "team": 490,
    "career": 650,
}
# (start_x, end_x) of a patch drawn *behind* each value to blot out the
# template's placeholder number before the real value is drawn on top.
COL_PATCH_X = {
    "batting": (108, 183),
    "bowling": (258, 340),
    "team": (418, 495),
    "career": (578, 655),
}
ROW_Y = {1: 518, 2: 558, 3: 598}
PATCH_HALF_HEIGHT = 14
PATCH_COLOR = (26, 11, 8)
RATING_POS = (178, 646)
RATING_PATCH = (172, 632, 340, 662)

VALUE_COLOR = (245, 231, 196)
RATING_COLOR = (255, 255, 255)


def _fmt(value) -> str:
    return str(value)


def _build_stat_fields(row) -> dict:
    strike_rate = round((row["runs_scored"] / row["balls_faced"]) * 100, 1) if row["balls_faced"] else 0.0
    batting_avg = round(row["runs_scored"] / row["times_out"], 1) if row["times_out"] else row["runs_scored"]
    economy = (
        round(row["runs_conceded"] / (row["balls_bowled"] / 6), 1) if row["balls_bowled"] else 0.0
    )
    best_bowling = f"{row['best_bowling']}" if row["best_bowling"] else "-"
    total_achievements = row["centuries"] + row["fifties"] + row["motm_awards"]

    highest = str(row["highest_score"])

    exp = compute_exp(row)
    current_level, _, _ = level_progress(exp)
    rating = current_level.split(" ")[0]  # strip the emoji, keep the plain word (Rookie/Pro/Elite/...)

    return {
        ("batting", 1): _fmt(highest),
        ("batting", 2): _fmt(batting_avg),
        ("batting", 3): _fmt(strike_rate),
        ("bowling", 1): _fmt(row["wickets_taken"]),
        ("bowling", 2): _fmt(economy),
        ("bowling", 3): _fmt(best_bowling),
        # NOTE: Cricket Royale has no fielding mechanic (catches / run outs /
        # stumpings aren't tracked), so these three read 0 by design.
        ("team", 1): "0",
        ("team", 2): "0",
        ("team", 3): "0",
        ("career", 1): _fmt(row["matches_played"]),
        ("career", 2): _fmt(row["motm_awards"]),
        ("career", 3): _fmt(total_achievements),
        "rating": rating,
    }


def _composite_sync(photo_bytes: Optional[bytes], row) -> Optional[bytes]:
    if not config.userstats_template().exists():
        logger.warning("Missing asset %s - skipping visual userstats card.", config.userstats_template())
        return None

    from PIL import Image, ImageDraw, ImageFont

    template = Image.open(config.userstats_template()).convert("RGBA")

    if photo_bytes:
        radius = config.USERSTATS_CIRCLE_RADIUS
        diameter = radius * 2
        cx, cy = config.USERSTATS_CIRCLE_CENTER

        profile = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
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

    draw = ImageDraw.Draw(template)
    try:
        value_font = ImageFont.truetype(str(config.USERSTATS_FONT), 22)
        rating_font = ImageFont.truetype(str(config.USERSTATS_FONT), 24)
    except OSError:
        logger.warning("Could not load %s - falling back to default font.", config.USERSTATS_FONT)
        value_font = ImageFont.load_default()
        rating_font = value_font

    fields = _build_stat_fields(row)

    # Blot out the template's placeholder numbers first, then draw the
    # real values on top of a clean patch.
    for col, row_num in [(c, r) for c in COL_PATCH_X for r in ROW_Y]:
        x0, x1 = COL_PATCH_X[col]
        y = ROW_Y[row_num]
        draw.rectangle((x0, y - PATCH_HALF_HEIGHT, x1, y + PATCH_HALF_HEIGHT), fill=PATCH_COLOR)
    draw.rectangle(RATING_PATCH, fill=PATCH_COLOR)

    for key, text in fields.items():
        if key == "rating":
            continue
        col, row_num = key
        x = COL_X[col]
        y = ROW_Y[row_num]
        draw.text((x, y), text, font=value_font, fill=VALUE_COLOR, anchor="rm")

    draw.text(RATING_POS, fields["rating"], font=rating_font, fill=RATING_COLOR, anchor="lm")

    buf = io.BytesIO()
    template.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


async def build_userstats_card(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, row
) -> Optional[bytes]:
    photo_bytes = None
    try:
        photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
        if photos.total_count > 0 and photos.photos:
            largest = photos.photos[0][-1]
            file = await context.bot.get_file(largest.file_id)
            raw = await file.download_as_bytearray()
            photo_bytes = bytes(raw)
    except TelegramError as exc:
        logger.debug("Could not fetch profile photo for %s: %s", user_id, exc)

    try:
        return await asyncio.to_thread(_composite_sync, photo_bytes, row)
    except Exception as exc:  # noqa: BLE001 - image processing can fail in many ways
        logger.warning("Userstats card compositing failed: %s", exc)
        return None
