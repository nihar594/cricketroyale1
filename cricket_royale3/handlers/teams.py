"""
/teams  - show the current lobby/match rosters and captains.
/add a  - join Team A during the lobby phase (command alternative to the button).
/add b  - join Team B during the lobby phase.
/remove - leave your team during the lobby phase.
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from handlers.start import get_store
from utils.models import MatchMode, MatchState, MatchStatus, Player, TeamKey
from utils.scoreboard import escape_md


def _player_match_tag(match: MatchState, team, player) -> str:
    """The '- (Out)' / '- (yet to bat)' / '- (Non Striker)' / '- (Bowling)'
    suffix shown next to a player in /teams, once the match is underway."""
    if match.status.name != "IN_PROGRESS":
        return ""

    is_batting_team = match.batting_team_key is not None and team.key == match.batting_team_key

    if is_batting_team:
        if player.user_id == match.striker_id:
            return ""  # striker is implied by appearing without a tag, like the sample
        if player.user_id == match.non_striker_id:
            return " - (Non Striker)"
        if player.is_out:
            return " - (Out)"
        return " - (yet to bat)"

    # Bowling side.
    if match.bowler_id and player.user_id == match.bowler_id:
        return " - (Bowling)"
    return ""


def _team_roster_block(match: MatchState, team, emoji: str) -> list:
    lines = [f"{emoji} TEAM {team.key.value}"]
    if not team.players:
        lines.append(" -")
        return lines

    for i, p in enumerate(team.players.values(), start=1):
        cap_tag = " (C) 👑" if p.user_id == team.captain_id else ""
        status_tag = _player_match_tag(match, team, p)
        lines.append(f" {i}. {escape_md(p.display_name)}{cap_tag}{status_tag}")
    return lines


async def teams_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active match or lobby in this chat.")
        return

    if match.mode is MatchMode.SOLO:
        lines = [f"🏏 *Solo Match Status:* {match.status.name}", ""]
        if match.royale_order:
            lines.append(f"👥 *Joined ({len(match.royale_order)}):*")
            for i, uid in enumerate(match.royale_order, start=1):
                p = match.royale_players[uid]
                tag = ""
                if match.status.name == "IN_PROGRESS" and uid == match.striker_id:
                    tag = " 🏏 (batting now)"
                lines.append(f"{i}. {escape_md(p.display_name)}{tag}")
        else:
            lines.append("No one has joined yet.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    lines = [
        "🏟️ TEAMS ROSTER",
        "✦ ──────────── ✦",
        "",
        *_team_roster_block(match, match.team_a, "🔴"),
        "",
        *_team_roster_block(match, match.team_b, "🔵"),
    ]
    if match.overs:
        lines.append("")
        lines.append(f"Overs: {match.overs}")
    if match.status.name == "IN_PROGRESS":
        lines.append(f"Score: {match.score}/{match.wickets} ({match.current_over}.{match.current_ball} ov)")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/add a  or  /add b  --  join that team yourself.

    The Host can additionally add *someone else* by:
      • replying to that person's message:      /add a
      • tagging them by username:                /add a @username
      • or by their numeric Telegram user ID:    /add a 123456789

    And the Host can bulk-add several players at once by listing more
    than one username/ID after the team letter (space- or newline-
    separated -- Telegram treats a multi-line message the same way):
      /add a @user1 @user2 123456789 @user3
    which replies with a single Add Report summarising who made it in
    and who didn't (already on a team, not found, team full, etc).

    Tagging by username only works if that person has used the bot
    before (e.g. /start), since Telegram doesn't resolve @usernames to
    an ID for us -- same limitation as /give.
    """
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active match or lobby in this chat.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text("Use /join for Solo Match, not /add.")
        return

    is_host = user.id == match.creator_id
    in_lobby = match.status == MatchStatus.LOBBY

    # Self-join (no reply, no username/ID -- just "/add a") only makes
    # sense during the lobby phase, before teams are locked in. Once the
    # match is underway, only the Host can bring someone new onto a
    # roster (they won't bat/bowl immediately -- just become available
    # for the next /batting or /bowling pick).
    if not in_lobby and not is_host:
        await update.message.reply_text(
            "The match is already in progress -- only the Host can add players now."
        )
        return

    if not context.args or context.args[0].lower() not in ("a", "b"):
        await update.message.reply_text(
            "Usage: /add a  or  /add b\n"
            "Host only -- add someone else: reply to their message with /add a, "
            "or /add a @username, or /add a <user_id>, or list several at once: "
            "/add a @user1 @user2 123456789"
        )
        return

    team_key = TeamKey.A if context.args[0].lower() == "a" else TeamKey.B
    target_team = match.team(team_key)
    other_team = match.team(team_key.other)

    extra_args = context.args[1:]
    replied_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None

    if len(extra_args) >= 2:
        if not is_host:
            await update.message.reply_text("Only the Host can add other players to a team.")
            return
        await _bulk_add(update, context, match, team_key, target_team, other_team, extra_args)
        return

    extra_arg = extra_args[0] if extra_args else None

    target_user_id: int
    target_username = None
    target_first_name = None
    added_by_host = False

    if (replied_user or extra_arg) and is_host:
        # Host is manually adding someone else.
        db = context.application.bot_data.get("db")
        if replied_user:
            target_user_id = replied_user.id
            target_username = replied_user.username
            target_first_name = replied_user.first_name
        else:
            if extra_arg.lstrip("@").isdigit():
                target_user_id = int(extra_arg.lstrip("@"))
                # Best-effort name lookup from the DB, since we only have the ID.
                row = await db.get_user_stats(target_user_id) if db else None
                target_username = row["username"] if row else None
                target_first_name = row["first_name"] if row else None
            else:
                row = await db.get_user_by_username(extra_arg) if db else None
                if row is None:
                    await update.message.reply_text(
                        f"Couldn't find {extra_arg} -- they need to have used the bot at least once "
                        "(e.g. /start) before you can add them this way. You can also reply "
                        "directly to their message with /add a or /add b instead."
                    )
                    return
                target_user_id = row["user_id"]
                target_username = row["username"]
                target_first_name = row["first_name"]
        added_by_host = True
    elif (replied_user or extra_arg) and not is_host:
        await update.message.reply_text("Only the Host can add other players to a team.")
        return
    else:
        # Plain /add a or /add b -- the caller is joining themself.
        target_user_id = user.id
        target_username = user.username
        target_first_name = user.first_name

    from handlers.owner import is_banned
    if await is_banned(context, target_user_id):
        if added_by_host:
            await update.message.reply_text("That player has been banned from Cricket Royale.")
        else:
            await update.message.reply_text("🚫 You've been banned from Cricket Royale.")
        return

    if target_user_id in other_team.players:
        msg = "They're already in the other team!" if added_by_host else "You're already in the other team!"
        await update.message.reply_text(msg)
        return
    if target_user_id in target_team.players:
        msg = f"They're already in {target_team.name}!" if added_by_host else f"You're already in {target_team.name}!"
        await update.message.reply_text(msg)
        return
    if target_team.size >= config.MAX_PLAYERS_PER_TEAM:
        await update.message.reply_text(f"{target_team.name} is full.")
        return

    new_player = Player(user_id=target_user_id, username=target_username, first_name=target_first_name or "Player")
    target_team.add_player(new_player)

    add_host_emoji = "🔵" if team_key is TeamKey.A else "🔴"
    if added_by_host:
        await update.message.reply_text(
            f"✅ - {escape_md(new_player.display_name)} has been manually added to TEAM {team_key.value} {add_host_emoji} "
            f"by the Host! 👥",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        # Plain name only, no @username tag/mention -- just an announcement.
        join_emoji = "🔴" if team_key is TeamKey.A else "🔵"
        await update.message.reply_text(
            f"{join_emoji} {new_player.first_name} joined Team {team_key.value}!"
        )

    if in_lobby:
        from handlers.lobby import _refresh_lobby_message, _auto_start_match
        import time
        seconds_left = max(0, int(config.LOBBY_TIMEOUT_SECONDS - (time.time() - match.created_at)))
        await _refresh_lobby_message(match, context, seconds_left)

        if match.team_a.size >= config.MIN_PLAYERS_PER_TEAM and match.team_b.size >= config.MIN_PLAYERS_PER_TEAM:
            await _auto_start_match(match, context)


async def _bulk_add(update, context, match, team_key, target_team, other_team, tokens: list) -> None:
    """Host bulk-add: /add a @user1 @user2 123456789 ... -- resolves each
    token independently and posts one Add Report summarising the batch,
    instead of a separate message per player."""
    db = context.application.bot_data.get("db")
    from handlers.owner import is_banned

    added: list = []
    failed: list = []  # (token, reason)

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        if token.lstrip("@").isdigit():
            target_user_id = int(token.lstrip("@"))
            row = await db.get_user_stats(target_user_id) if db else None
            target_username = row["username"] if row else None
            target_first_name = row["first_name"] if row else None
            if row is None:
                target_first_name = target_first_name or "Player"
        else:
            row = await db.get_user_by_username(token) if db else None
            if row is None:
                failed.append((token, "not found -- must have used the bot before"))
                continue
            target_user_id = row["user_id"]
            target_username = row["username"]
            target_first_name = row["first_name"]

        if await is_banned(context, target_user_id):
            failed.append((token, "banned"))
            continue
        if target_user_id in other_team.players:
            failed.append((token, "already in the other team"))
            continue
        if target_user_id in target_team.players:
            failed.append((token, f"already in {target_team.name}"))
            continue
        if target_team.size >= config.MAX_PLAYERS_PER_TEAM:
            failed.append((token, f"{target_team.name} is full"))
            continue

        new_player = Player(
            user_id=target_user_id, username=target_username, first_name=target_first_name or "Player"
        )
        target_team.add_player(new_player)
        added.append(new_player)

    team_emoji = "🔴" if team_key is TeamKey.A else "🔵"
    lines = [f"📋 Add Report — TEAM {team_key.value} {team_emoji}", ""]

    if added:
        lines.append(f"✅ Added ({len(added)}):")
        for p in added:
            lines.append(f"  • {escape_md(p.display_name)}")
    if failed:
        if added:
            lines.append("")
        lines.append(f"❌ Failed ({len(failed)}):")
        for token, reason in failed:
            lines.append(f"  • {escape_md(token)} — {reason}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    if match.status == MatchStatus.LOBBY:
        from handlers.lobby import _refresh_lobby_message, _auto_start_match
        import time
        seconds_left = max(0, int(config.LOBBY_TIMEOUT_SECONDS - (time.time() - match.created_at)))
        await _refresh_lobby_message(match, context, seconds_left)

        if match.team_a.size >= config.MIN_PLAYERS_PER_TEAM and match.team_b.size >= config.MIN_PLAYERS_PER_TEAM:
            await _auto_start_match(match, context)


async def leaveteam_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lets a player voluntarily leave the Team Match lobby they joined
    themself -- /remove is Host-only and used to kick someone else."""
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None or match.status != MatchStatus.LOBBY:
        await update.message.reply_text("There's no open lobby to leave right now.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text("Use /leavesolo for Solo Match, not /leaveteam.")
        return

    team = match.find_team_of(user.id)
    if team is None:
        await update.message.reply_text("You're not in a team yet.")
        return

    player = team.players.get(user.id)
    player_name = player.first_name if player else (user.first_name or "Player")

    team.remove_player(user.id)
    await update.message.reply_text(f"🚪 {player_name} left the lobby.")

    from handlers.lobby import _refresh_lobby_message
    import time
    seconds_left = max(0, int(config.LOBBY_TIMEOUT_SECONDS - (time.time() - match.created_at)))
    await _refresh_lobby_message(match, context, seconds_left)


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Host-only: kick a player out of the current Team Match lobby/match.

      • reply to that player's message with /remove
      • /remove @username
      • /remove <user_id>
    """
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active Team Match right now.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text("Use /leavesolo for Solo Match, not /remove.")
        return

    if user.id != match.creator_id:
        await update.message.reply_text("Only the Host can remove players from the match.")
        return

    extra_arg = context.args[0] if context.args else None
    replied_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None

    if not replied_user and not extra_arg:
        await update.message.reply_text(
            "Usage: reply to a player's message with /remove, or /remove @username, "
            "or /remove <user_id>."
        )
        return

    if replied_user:
        target_user_id = replied_user.id
    elif extra_arg.lstrip("@").isdigit():
        target_user_id = int(extra_arg.lstrip("@"))
    else:
        db = context.application.bot_data.get("db")
        row = await db.get_user_by_username(extra_arg) if db else None
        if row is None:
            await update.message.reply_text(
                f"Couldn't find {extra_arg} -- they need to have used the bot at least once "
                "(e.g. /start) before you can remove them this way. You can also reply "
                "directly to their message with /remove instead."
            )
            return
        target_user_id = row["user_id"]

    team = match.find_team_of(target_user_id)
    if team is None:
        await update.message.reply_text("That player isn't in either team.")
        return

    removed_player = team.players.get(target_user_id)
    removed_name = removed_player.display_name if removed_player else "That player"

    if match.status == MatchStatus.LOBBY:
        team.remove_player(target_user_id)
        await update.message.reply_text(f"👋 {removed_name} has been removed from the match by the Host.")

        from handlers.lobby import _refresh_lobby_message
        import time
        seconds_left = max(0, int(config.LOBBY_TIMEOUT_SECONDS - (time.time() - match.created_at)))
        await _refresh_lobby_message(match, context, seconds_left)
        return

    # Match already in progress -- removing an active striker/non-striker/
    # bowler mid-match would leave the innings in a broken state, so that's
    # not supported here; only players not currently out in the middle can
    # be safely pulled from the roster.
    if target_user_id in (match.striker_id, match.non_striker_id, match.bowler_id):
        await update.message.reply_text(
            f"{removed_name} is currently batting/bowling and can't be removed mid-delivery. "
            "Wait until they're out or their over ends, then try again."
        )
        return

    team.remove_player(target_user_id)
    await update.message.reply_text(f"👋 {removed_name} has been removed from the match by the Host.")


async def changehost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Host-only: hand Host privileges to another player already in Team A
    or Team B, either by:
      • replying to that player's message with /changehost
      • /changehost @username
      • /changehost <user_id>
    """
    chat = update.effective_chat
    user = update.effective_user
    store = get_store(context)
    match = store.get(chat.id)

    if match is None:
        await update.message.reply_text("There's no active match or lobby in this chat.")
        return

    if match.mode is MatchMode.SOLO:
        await update.message.reply_text("Solo Match doesn't have a Host to transfer.")
        return

    if match.creator_id is None:
        await update.message.reply_text("This match doesn't have a Host yet.")
        return

    if user.id != match.creator_id:
        await update.message.reply_text("Only the current Host can transfer Host privileges.")
        return

    extra_arg = context.args[0] if context.args else None
    replied_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None

    if not replied_user and not extra_arg:
        await update.message.reply_text(
            "Reply to the player's message with /changehost, or /changehost @username, "
            "or /changehost <user_id>, to make them the new Host."
        )
        return

    if replied_user:
        target_user_id = replied_user.id
    elif extra_arg.lstrip("@").isdigit():
        target_user_id = int(extra_arg.lstrip("@"))
    else:
        db = context.application.bot_data.get("db")
        row = await db.get_user_by_username(extra_arg) if db else None
        if row is None:
            await update.message.reply_text(
                f"Couldn't find {extra_arg} -- they need to have used the bot at least once "
                "(e.g. /start) before you can make them Host this way. You can also reply "
                "directly to their message with /changehost instead."
            )
            return
        target_user_id = row["user_id"]

    if target_user_id == user.id:
        await update.message.reply_text("You're already the Host!")
        return

    team = match.find_team_of(target_user_id)
    if team is None:
        await update.message.reply_text(
            "That player isn't in Team A or Team B -- they need to join a team first."
        )
        return

    new_host = team.players[target_user_id]

    match.creator_id = new_host.user_id
    match.host_display_name = new_host.display_name

    await update.message.reply_text(
        f"✅ Host privileges successfully transferred to {escape_md(new_host.display_name)}! 👑",
        parse_mode=ParseMode.MARKDOWN,
    )
