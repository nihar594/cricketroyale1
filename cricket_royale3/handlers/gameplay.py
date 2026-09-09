"""
The heart of Cricket Royale: resolving each ball, driving overs/innings
transitions, and wiring the bowler-DM / batter-in-group input flow.

Same-number-as-batter => wicket. Different numbers => runs equal to the
batter's number. Strike rotates on 1, 3 and 5 (Team Match only -- Solo
Match/Royale mode has a single batter at a time, so there's no partner to
rotate strike with).

Team Match and Solo Match (Royale mode) share this exact engine: Royale
mode's handlers/solo.py repurposes team_a as a 1-player "team" holding
whoever is currently batting, and team_b as the pool of everyone else who
bowls that mini-innings -- so all the plumbing below works unmodified for
both modes.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.commentary import get_commentary
from utils.keyboards import bowl_now_keyboard
from utils.media import send_gif_or_text
from utils.models import MatchMode, MatchState, MatchStatus, TeamKey

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Lineup readiness -> prompt bowler to bowl
# --------------------------------------------------------------------------
async def maybe_ready_to_bowl(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Once striker, (non-striker if the batting side has 2+ players) and
    bowler are all set, surface the 'Bowl Now' prompt for the next
    delivery. Royale mode's batting side is always a single player, so the
    non-striker requirement naturally never blocks it."""
    if match.awaiting_bowler_input or match.awaiting_batter_input:
        return
    if not (match.striker_id and match.bowler_id):
        return
    if match.batting_team.size > 1 and not match.non_striker_id:
        return

    match.status = MatchStatus.IN_PROGRESS

    from utils.scoreboard import format_bowl_prompt
    caption = format_bowl_prompt(match)
    bot_username = context.application.bot_data.get("bot_username") or (await context.bot.get_me()).username
    await send_gif_or_text(
        context=context, chat_id=match.chat_id, gif_path=config.bowling_gif(),
        caption=caption, reply_markup=bowl_now_keyboard(bot_username),
    )

    # The 60-second bowler clock starts the moment this prompt is shown --
    # not when they eventually tap the button and open the DM -- otherwise
    # someone who never clicks at all would never time out.
    match.awaiting_bowler_input = True
    if match.mode is MatchMode.SOLO:
        from handlers.solo import schedule_bowler_timeout
        schedule_bowler_timeout(match, context)
    elif match.mode is MatchMode.TEAM:
        from handlers.team_timeout import schedule_bowler_timeout as schedule_team_bowler_timeout
        schedule_team_bowler_timeout(match, context)


async def handle_bowl_deeplink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fired when the bowler taps the 'Bowl Now' URL button, which deep-links
    straight into a private chat with the bot and auto-sends /start bowl.
    Finds whichever match this user is currently the ready bowler for --
    the 60s clock already started when the prompt first appeared in the
    group (see maybe_ready_to_bowl), this just shows them the DM prompt."""
    user = update.effective_user
    store = get_store(context)

    target_match = None
    for match in store.all():
        if (
            match.status == MatchStatus.IN_PROGRESS
            and match.bowler_id == user.id
            and match.awaiting_bowler_input
        ):
            target_match = match
            break

    if target_match is None:
        await update.message.reply_text(
            "No active delivery found for you to bowl right now. "
            "Go back to the group and try again from there."
        )
        return

    from utils.scoreboard import format_dm_bowl_prompt
    keyboard = None
    if target_match.chat_username:
        from utils.keyboards import back_to_game_keyboard
        keyboard = back_to_game_keyboard(target_match.chat_username)

    await update.message.reply_text(
        format_dm_bowl_prompt(target_match), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard,
    )


# --------------------------------------------------------------------------
# Unified numeric input router: bowler DM input (1-6) + batter group input
# (0-6). Works identically for Team Match and Solo Match/Royale mode, since
# both play out in team_a (batter) / team_b (bowler) pseudo-teams.
# --------------------------------------------------------------------------
async def numeric_input_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text.isdigit():
        return
    num = int(text)
    if num < 0 or num > 6:
        return

    user = update.effective_user
    chat = update.effective_chat
    store = get_store(context)

    # Private chat: the bowler entering their delivery via DM.
    if chat.type == "private":
        pending_match = store.find_by_pending_bowler(user.id)
        if pending_match is not None:
            if num < 1:
                await update.message.reply_text("Please enter a number from 1 to 6.")
                return
            if pending_match.spam_free_mode and len(pending_match.recent_bowler_numbers) == 2 and \
                    pending_match.recent_bowler_numbers[0] == pending_match.recent_bowler_numbers[1] == num:
                await update.message.reply_text(
                    f"🛡️ Spam-free mode is active -- you can't bowl {num} a third time in a row. "
                    "Pick a different number."
                )
                return
            await _human_bowls(pending_match, context, num)
            return

    # Group chat: the striker playing their shot.
    if chat.type in ("group", "supergroup"):
        match = store.get(chat.id)
        if match is not None and match.awaiting_batter_input and user.id == match.striker_id:
            if match.mode is MatchMode.SOLO and num == 0:
                await update.message.reply_text("Solo Match has no dot balls -- type a number from 1 to 6.")
                return
            await resolve_ball(match, context, num)
            return


async def _human_bowls(match: MatchState, context: ContextTypes.DEFAULT_TYPE, num: int) -> None:
    match.pending_bowler_number = num
    match.awaiting_bowler_input = False
    match.awaiting_batter_input = True

    match.recent_bowler_numbers.append(num)
    match.recent_bowler_numbers = match.recent_bowler_numbers[-2:]

    striker = match.batting_team.players[match.striker_id]
    await update_bowler_locked_notice(context, match, num)

    if match.mode is MatchMode.SOLO:
        from handlers.solo import cancel_bowler_timeout_jobs, schedule_batter_timeout
        cancel_bowler_timeout_jobs(match, context)
        schedule_batter_timeout(match, context)
    elif match.mode is MatchMode.TEAM:
        from handlers.team_timeout import (
            cancel_bowler_timeout_jobs as cancel_team_bowler_timeout_jobs,
            schedule_batter_timeout as schedule_team_batter_timeout,
        )
        cancel_team_bowler_timeout_jobs(match, context)
        schedule_team_batter_timeout(match, context)

    from utils.scoreboard import format_ball_delivered
    caption = format_ball_delivered(striker.display_name, match.mode)
    await send_gif_or_text(
        context=context, chat_id=match.chat_id, gif_path=config.ball_delivered_gif(), caption=caption,
    )


async def update_bowler_locked_notice(context: ContextTypes.DEFAULT_TYPE, match: MatchState, num: int) -> None:
    keyboard = None
    if match.chat_username:
        from utils.keyboards import back_to_game_keyboard
        keyboard = back_to_game_keyboard(match.chat_username)
    try:
        await context.bot.send_message(
            chat_id=match.bowler_id,
            text=f"Choice locked! 🍁 You bowled a {num}.",
            reply_markup=keyboard,
        )
    except Forbidden:
        pass


# --------------------------------------------------------------------------
# Ball resolution
# --------------------------------------------------------------------------
async def resolve_ball(match: MatchState, context: ContextTypes.DEFAULT_TYPE, batter_num: int) -> None:
    if match.mode is MatchMode.SOLO:
        from handlers.solo import cancel_batter_timeout_jobs
        cancel_batter_timeout_jobs(match, context)
    elif match.mode is MatchMode.TEAM:
        from handlers.team_timeout import cancel_batter_timeout_jobs as cancel_team_batter_timeout_jobs
        cancel_team_batter_timeout_jobs(match, context)

    from utils.scoreboard import escape_md

    bowling_team = match.bowling_team
    batting_team = match.batting_team
    bowler = bowling_team.players[match.bowler_id]
    striker = batting_team.players[match.striker_id]
    striker_name = escape_md(striker.display_name)  # capture before any strike swap below

    bowler_num = match.pending_bowler_number
    is_wicket = bowler_num == batter_num
    strike_rotated_to: Optional[str] = None
    milestone: Optional[str] = None

    bowler.balls_bowled += 1
    bowler.current_spell_balls += 1
    striker.balls_faced += 1

    if is_wicket:
        striker.is_out = True
        bowler.wickets_taken += 1
        bowler.current_spell_wickets += 1
        match.wickets += 1
        ball_symbol = "W"
        gif_path = config.wicket_gif()
        commentary = get_commentary(0, True, name=escape_md(bowler.display_name))
    else:
        runs = batter_num
        runs_before = striker.runs
        match.score += runs
        striker.runs += runs
        bowler.runs_conceded += runs
        bowler.current_spell_runs += runs
        if runs == 4:
            striker.fours += 1
        elif runs == 6:
            striker.sixes += 1
        ball_symbol = str(runs)
        gif_path = config.run_gif(runs)
        commentary = get_commentary(runs, False)
        if match.mode is MatchMode.TEAM and runs in config.STRIKE_ROTATING_RUNS and match.non_striker_id:
            match.striker_id, match.non_striker_id = match.non_striker_id, match.striker_id
            strike_rotated_to = escape_md(batting_team.players[match.striker_id].display_name)

        if runs_before < 100 <= striker.runs:
            milestone = "century"
        elif runs_before < 50 <= striker.runs:
            milestone = "fifty"

    match.this_over_balls.append(ball_symbol)
    match.current_ball += 1
    match.awaiting_bowler_input = False
    match.awaiting_batter_input = False
    match.pending_bowler_number = None

    if is_wicket:
        caption = (
            f"╔══ 🏏 *WICKET!* 🏏 ══╗\n"
            f"┃\n"
            f"┃ 😵 Bowler: *{bowler_num}*  |  Batter: *{batter_num}*\n"
            f"┃\n"
            f"┃ ❌ *THAT'S OUT!!* ❌\n"
            f"┃\n"
            f"┃ {commentary}\n"
            f"┃\n"
            f"┃ 👻 {striker_name} departs for *{striker.runs}* ({striker.balls_faced}b)\n"
            f"╚═══════════════╝"
        )
    else:
        footer = f"({striker_name}: {striker.runs} off {striker.balls_faced})"
        caption = f"🏏 *Batter hit:* {batter_num}\n\n{commentary}\n{footer}"

    await send_gif_or_text(
        context=context, chat_id=match.chat_id, gif_path=gif_path, caption=caption
    )

    if is_wicket and striker.runs == 0:
        await send_gif_or_text(
            context=context, chat_id=match.chat_id, gif_path=config.duck_gif(),
            caption=(
                f"🦆 *DUCK!* 🦆\n\n"
                f"{striker_name} departs *without troubling the scorers* — "
                f"out for a golden zero! 😬"
            ),
        )

    if milestone == "century":
        await send_gif_or_text(
            context=context, chat_id=match.chat_id, gif_path=config.century_gif(),
            caption=(
                f"💯 *CENTURY!* {striker_name} brings up a magnificent "
                f"*{striker.runs}* off {striker.balls_faced} balls! 🎉🏆"
            ),
        )
    elif milestone == "fifty":
        await send_gif_or_text(
            context=context, chat_id=match.chat_id, gif_path=config.fifty_gif(),
            caption=(
                f"5️⃣0️⃣ *HALF-CENTURY!* {striker_name} raises the bat for a "
                f"brilliant *{striker.runs}* off {striker.balls_faced} balls! 👏"
            ),
        )

    if strike_rotated_to:
        await context.bot.send_message(
            chat_id=match.chat_id,
            text=f"🔄 Strike rotated! 🏏 {strike_rotated_to} is now on strike!",
        )

    over_complete = match.current_ball >= config.BALLS_PER_OVER
    completed_over_number = None
    completed_over_balls = None
    completed_over_bowler = None
    completed_over_spell = None
    if over_complete:
        # Capture everything about the over that just finished *before*
        # it gets reset/rolled into spells, so the over-summary box below
        # has the exact ball-by-ball sequence and figures for this over.
        completed_over_number = match.current_over + 1
        completed_over_balls = list(match.this_over_balls)
        completed_over_bowler = bowler
        completed_over_spell = {
            "balls": bowler.current_spell_balls,
            "runs": bowler.current_spell_runs,
            "wickets": bowler.current_spell_wickets,
        }

        match.current_over += 1
        match.current_ball = 0
        match.this_over_balls = []
        bowler.spells.append(completed_over_spell)
        bowler.current_spell_balls = 0
        bowler.current_spell_runs = 0
        bowler.current_spell_wickets = 0

    if match.mode is MatchMode.TEAM:
        if over_complete:
            match.last_bowler_id = match.bowler_id
            match.bowler_id = None
        if is_wicket:
            # The striker who was just out leaves the crease. If this also
            # ends the over, the not-out batter (currently non-striker)
            # rotates onto strike for the new over, leaving the non-striker
            # slot open for the incoming batter. Otherwise the incoming
            # batter simply fills the now-vacant striker slot.
            if over_complete and match.non_striker_id:
                match.striker_id = match.non_striker_id
                match.non_striker_id = None
            else:
                match.striker_id = None
        elif over_complete and match.non_striker_id:
            # Normal end-of-over strike rotation (no wicket on the last ball).
            match.striker_id, match.non_striker_id = match.non_striker_id, match.striker_id

    from utils.scoreboard import format_scoreboard, format_over_summary
    if over_complete and match.mode is MatchMode.TEAM and not is_wicket:
        # The boxed over-recap replaces the plain scoreboard right when an
        # over ends (mid-over deliveries still get the normal scoreboard).
        # Skipped on a last-ball wicket since end_innings/next-batter
        # messaging takes over instead.
        await context.bot.send_message(
            chat_id=match.chat_id,
            text=format_over_summary(
                match, completed_over_number, completed_over_bowler,
                completed_over_balls, completed_over_spell,
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await context.bot.send_message(
            chat_id=match.chat_id, text=format_scoreboard(match), parse_mode=ParseMode.MARKDOWN
        )

    trailing_notices = []
    if (
        over_complete and match.mode is MatchMode.TEAM and not is_wicket
        and match.non_striker_id and match.striker_id
    ):
        # Announce the automatic end-of-over strike swap (distinct from the
        # mid-over 1/3/5-runs rotation message above).
        new_striker = batting_team.players[match.striker_id]
        trailing_notices.append(f"🔄 Strike rotated! 🏏 {new_striker.display_name} is on strike!")

    if match.mode is MatchMode.TEAM:
        all_out = match.wickets >= batting_team.size - 1
        overs_done = match.current_over >= match.overs
        target_reached = match.target is not None and match.score >= match.target

        if target_reached or overs_done or all_out:
            if trailing_notices:
                # Only fire if there's actually something to say -- the
                # innings-end message that follows covers everything else.
                await context.bot.send_message(chat_id=match.chat_id, text="\n".join(trailing_notices))
            await end_innings(match, context)
            return

        if is_wicket:
            cap = batting_team.players[batting_team.captain_id]
            trailing_notices.append(f"🏏 {cap.display_name}, use /batting to send in the next batter.")
        if over_complete:
            cap = bowling_team.players[bowling_team.captain_id]
            trailing_notices.append(f"🎯 {cap.display_name}, use /bowling to pick the next bowler.")

        # One combined message instead of up to three separate ones --
        # same information, same reading order, far fewer Telegram API
        # round-trips per ball (each round-trip is real, unavoidable
        # network latency on top of anything the server itself does).
        if trailing_notices:
            await context.bot.send_message(chat_id=match.chat_id, text="\n".join(trailing_notices))

        await maybe_ready_to_bowl(match, context)
        return

    # ------------------------------------------------------------------
    # Solo Match / Royale mode: no captains, no overs cap, no target -- a
    # single wicket ends that player's turn, and the next batter/bowler is
    # picked automatically by handlers/solo.py's join-order rotation.
    # ------------------------------------------------------------------
    from handlers.solo import advance_royale_batter, advance_royale_bowler

    if is_wicket:
        await advance_royale_batter(match, context)
    else:
        if over_complete:
            await advance_royale_bowler(match, context)
        await maybe_ready_to_bowl(match, context)


# --------------------------------------------------------------------------
# Innings / match completion
# --------------------------------------------------------------------------
async def end_innings(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Team Match only -- Royale/Solo mode's per-batter turns are handled
    entirely by handlers/solo.py's advance_royale_batter, which never
    routes through here."""
    from handlers.team_timeout import cancel_bowler_timeout_jobs, cancel_batter_timeout_jobs
    cancel_bowler_timeout_jobs(match, context)
    cancel_batter_timeout_jobs(match, context)

    from utils.scoreboard import format_innings_summary

    finished_team = match.batting_team
    summary_text = format_innings_summary(match, finished_team)
    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"🏁 *Innings Complete!*\n\n{summary_text}\n\nFinal: {match.score}/{match.wickets}",
        parse_mode=ParseMode.MARKDOWN,
    )

    if match.innings == 1:
        match.innings_1_summary = {
            "team_key": match.batting_team_key,
            "score": match.score,
            "wickets": match.wickets,
        }
        target = match.score + 1
        match.batting_team_key = match.batting_team_key.other
        match.innings = 2
        match.target = target
        match.score = 0
        match.wickets = 0
        match.current_over = 0
        match.current_ball = 0
        match.this_over_balls = []
        match.striker_id = None
        match.non_striker_id = None
        match.bowler_id = None
        match.last_bowler_id = None
        match.status = MatchStatus.AWAITING_LINEUP

        for p in match.batting_team.players.values():
            p.is_out = False  # fresh innings for the new batting side

        await context.bot.send_message(
            chat_id=match.chat_id,
            text=(
                "🛑 INNINGS BREAK\n"
                "✦ ──────────── ✦\n\n"
                "Now chase!! 🔥\n"
                f"🎯 Target: {target} runs in {match.overs} overs\n\n"
                "🏏 Batting Captain/Host — send your openers:\n"
                "└ /batting [number] (do it twice)"
            ),
        )
    else:
        await finish_match(match, context)


async def finish_match(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = get_store(context)
    db = context.application.bot_data["db"]

    team1_key = match.innings_1_summary["team_key"]
    team1_score = match.innings_1_summary["score"]
    team2_key = match.batting_team_key
    team2_score = match.score

    if team2_score > team1_score:
        winner_key = team2_key
        margin = f"won by {match.team(team2_key).size - match.wickets} wicket(s)"
    elif team1_score > team2_score:
        winner_key = team1_key
        margin = f"won by {team1_score - team2_score} run(s)"
    else:
        winner_key = None
        margin = "Match tied!"

    winner_team = match.team(winner_key) if winner_key else None

    from utils.scoreboard import escape_md
    result_text = (
        "🏆✨ *MATCH COMPLETE!* ✨🏆\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🅰️ {escape_md(match.team(team1_key).name)}: *{team1_score}*\n"
        f"🅱️ {escape_md(match.team(team2_key).name)}: *{team2_score}*\n\n"
    )
    if winner_team:
        result_text += f"🎉 *{escape_md(winner_team.name)} {margin}!* 🎉"
    else:
        result_text += f"🤝 {margin}"

    await context.bot.send_message(chat_id=match.chat_id, text=result_text, parse_mode=ParseMode.MARKDOWN)

    all_players = list(match.team_a.players.values()) + list(match.team_b.players.values())

    from utils.player_of_match import pick_player_of_the_match, send_player_of_the_match
    pom = pick_player_of_the_match(all_players)
    if pom:
        await send_player_of_the_match(context, match.chat_id, pom)

    # Gems rewards -- every player on the winning side, plus a separate
    # bonus for whoever was picked Player of the Match.
    if winner_team:
        for p in winner_team.players.values():
            await db.add_gems(p.user_id, config.WINNER_GEMS_REWARD, p.username, p.first_name)
    if pom:
        await db.add_gems(pom.user_id, config.POTM_GEMS_REWARD, pom.username, pom.first_name)

    from handlers.economy import send_match_rewards
    await send_match_rewards(
        context=context,
        chat_id=match.chat_id,
        winner_name=winner_team.name if winner_team else "Tie",
        potm_name=pom.display_name if pom else "N/A",
        winner_coins=config.WINNER_GEMS_REWARD,
        potm_coins=config.POTM_GEMS_REWARD,
    )

    for p in all_players:
        team_of_p = match.team_a if p.user_id in match.team_a.players else match.team_b
        won = winner_key is not None and team_of_p.key == winner_key
        is_fifty = 50 <= p.runs < 100
        is_century = p.runs >= 100
        is_duck = p.is_out and p.runs == 0
        is_motm = pom is not None and p.user_id == pom.user_id
        await db.record_player_result(
            user_id=p.user_id,
            username=p.username,
            first_name=p.first_name,
            runs=p.runs,
            balls_faced=p.balls_faced,
            wickets=p.wickets_taken,
            balls_bowled=p.balls_bowled,
            runs_conceded=p.runs_conceded,
            won=won,
            fours=p.fours,
            sixes=p.sixes,
            is_fifty=is_fifty,
            is_century=is_century,
            is_duck=is_duck,
            was_out=p.is_out,
            is_solo=False,
            is_motm=is_motm,
        )

    await db.log_match(
        match_id=match.match_id,
        chat_id=match.chat_id,
        mode=match.mode.value,
        overs=match.overs,
        winner=winner_team.name if winner_team else "Tie",
        team_a_score=team1_score if team1_key == TeamKey.A else team2_score,
        team_b_score=team2_score if team1_key == TeamKey.A else team1_score,
    )

    match.status = MatchStatus.COMPLETED
    store.remove(match.chat_id)
