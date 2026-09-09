"""
Formats the live scoreboard message shown in the group chat.
"""

from utils.models import MatchMode, MatchState

DIVIDER = "━━━━━━━━━━━━━━━━━━"

_MD_SPECIAL_CHARS = "_*`["


def escape_md(text: str) -> str:
    """Escapes characters that break Telegram's legacy Markdown parser
    (_, *, `, [) so that fancy/emoji-heavy player names never crash a
    send_message call using ParseMode.MARKDOWN. Names are free-form user
    input (Telegram first names/usernames), so this must run on every
    name interpolated into a Markdown-formatted string."""
    if not text:
        return text
    for ch in _MD_SPECIAL_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


def _overs_display(match: MatchState) -> str:
    if match.mode is MatchMode.SOLO:
        return f"{match.current_over}.{match.current_ball}"
    return f"{match.current_over}.{match.current_ball}/{match.overs}"


def format_over_summary(
    match: MatchState,
    completed_over_number: int,
    bowler,
    over_balls: list,
    over_spell: dict,
) -> str:
    """The boxed 'OVER X/Y' recap card shown right after an over finishes
    -- bowler figures for that over, the ball-by-ball sequence with a
    4s/6s tally, the chase block (2nd innings only), and both batters
    with the striker marked."""
    batting = match.batting_team

    fours = sum(1 for b in over_balls if b == "4")
    sixes = sum(1 for b in over_balls if b == "6")
    balls_display = " | ".join(over_balls)

    lines = [
        f"╔══🏏 OVER {completed_over_number}/{match.overs} 🏏 ══╗",
        f"┃ 📊 Score: {match.score}/{match.wickets}",
        "┃",
        f"┃ 🎳 {escape_md(bowler.display_name)}",
        f"┃    Runs: {over_spell['runs']}",
        f"┃    Wkts: {over_spell['wickets']}",
        "┃",
        "┃ 🔢 THIS OVER",
        f"┃    {over_spell['runs']}R | {over_spell['wickets']}W",
        f"┃    4️⃣ {fours} | 6️⃣ {sixes}",
        f"┃    🎯 {balls_display}",
        "┃",
    ]

    if match.target is not None:
        remaining = max(match.target - match.score, 0)
        balls_left = max(match.balls_remaining(), 0)
        req_rr = match.required_run_rate()
        lines.append("┃ 🎯 CHASE")
        lines.append(f"┃    Need: {remaining}({balls_left})")
        if req_rr is not None:
            lines.append(f"┃    RRR: {req_rr}")
        lines.append("┃")

    lines.append("┃ 🏏 BATTERS")
    striker = batting.players.get(match.striker_id) if match.striker_id else None
    non_striker = batting.players.get(match.non_striker_id) if match.non_striker_id else None

    for p, is_striker in ((striker, True), (non_striker, False)):
        if not p:
            continue
        marker = "👉🏻" if is_striker else "•"
        suffix = " (\\*)" if is_striker else ""
        sr = round((p.runs / p.balls_faced) * 100) if p.balls_faced else 0
        lines.append(f"┃ {marker} {escape_md(p.display_name)}{suffix}")
        lines.append(f"┃    {p.runs}({p.balls_faced})")
        lines.append(f"┃    SR: {sr}")

    lines.append("╚════════════════╝")

    return "\n".join(lines)


def format_scoreboard(match: MatchState) -> str:
    batting = match.batting_team
    bowling = match.bowling_team

    striker = batting.players.get(match.striker_id) if match.striker_id else None
    non_striker = batting.players.get(match.non_striker_id) if match.non_striker_id else None
    bowler = bowling.players.get(match.bowler_id) if match.bowler_id else None

    if match.mode is MatchMode.SOLO:
        name = escape_md(striker.display_name) if striker else "Batter"
        header = f"🏏 *{name}* batting — *{match.score}/{match.wickets}*  ({_overs_display(match)} ov)"
    else:
        header = f"🏏 *{escape_md(batting.name)}* *{match.score}/{match.wickets}*  ({_overs_display(match)} ov)"

    lines = [
        "⚡ *LIVE SCOREBOARD* ⚡",
        DIVIDER,
        header,
        f"📈 Run rate: {match.run_rate()}",
    ]

    if match.target is not None:
        req_rr = match.required_run_rate()
        remaining = max(match.target - match.score, 0)
        lines.append(f"🎯 Target: {match.target}  |  Need {remaining} runs")
        if req_rr is not None:
            lines.append(f"📊 Required run rate: {req_rr}")

    lines.append("")

    if striker and match.mode is not MatchMode.SOLO:
        lines.append(f"🔸 *{escape_md(striker.display_name)}*  {striker.runs} ({striker.balls_faced}b)")
    if non_striker:
        lines.append(f"◾ {escape_md(non_striker.display_name)}  {non_striker.runs} ({non_striker.balls_faced}b)")
    if bowler:
        overs_bowled = bowler.balls_bowled // 6
        balls_part = bowler.balls_bowled % 6
        lines.append(
            f"🎯 Bowler: *{escape_md(bowler.display_name)}*  "
            f"{overs_bowled}.{balls_part}-{bowler.runs_conceded}-{bowler.wickets_taken}"
        )

    if match.this_over_balls:
        lines.append("")
        lines.append("🔴 This over: " + " ".join(match.this_over_balls))

    return "\n".join(lines)


def format_dm_bowl_prompt(match: MatchState) -> str:
    """The message shown inside the bowler's private chat with the bot,
    right after they deep-link in to bowl -- mirrors the 'Match in
    Progress! / Batter: X / Over Status: A.B / Your Turn to Bowl!' style."""
    batting = match.batting_team
    striker = batting.players.get(match.striker_id) if match.striker_id else None

    over_display = f"{match.current_over}.{match.current_ball}"
    if match.mode is not MatchMode.SOLO and match.overs:
        over_display += f" / {match.overs}"

    lines = ["🏏✨ *Match in Progress!* ✨🏏", DIVIDER, ""]
    if striker:
        lines.append(f"🏏 Batter: *{escape_md(striker.display_name)}*! ({striker.runs} off {striker.balls_faced})")
    lines.append(f"🥎 Over Status: {over_display}.")
    lines.append("")
    lines.append("👉 *Your Turn to Bowl!* Type a number from 1 to 6.")
    return "\n".join(lines)


def format_bowl_prompt(match: MatchState) -> str:
    """Compact status shown alongside the bowling GIF, right before the
    current bowler is prompted to bowl -- mirrors the 'Batter / Bowler /
    check your DM' style status card."""
    batting = match.batting_team
    bowling = match.bowling_team
    striker = batting.players.get(match.striker_id) if match.striker_id else None
    bowler = bowling.players.get(match.bowler_id) if match.bowler_id else None

    over_display = f"{match.current_over}.{match.current_ball}"
    if match.mode is not MatchMode.SOLO and match.overs:
        over_display += f" / {match.overs}"

    lines = ["📊 *Status* 📊", DIVIDER]
    if striker:
        lines.append(f"🏏 Batter: *{escape_md(striker.display_name)}* ({striker.runs} off {striker.balls_faced})")
    if bowler:
        lines.append(f"🥎 Bowler: *{escape_md(bowler.display_name)}* (Over: {over_display})")
    lines.append("")
    if bowler:
        lines.append(f"👉 {escape_md(bowler.display_name)}, check your DM to bowl! 🤫🥎")
    return "\n".join(lines)


def format_batter_arrival(name: str) -> str:
    """Shown with a short GIF whenever a new batter walks out to the
    crease -- start of an innings, or a fresh batter after a wicket."""
    return f"🏏✨ *{escape_md(name)}* walks out to bat! ✨🏏\nAll eyes on the crease... 👀"


def format_ball_delivered(striker_name: str, mode: MatchMode = MatchMode.TEAM) -> str:
    """Shown in the group right after the bowler has locked in their
    delivery via DM, prompting the striker to type their shot. Solo Match
    (Royale mode) excludes 0 -- every ball scores at least 1 run there."""
    range_text = "1-6" if mode is MatchMode.SOLO else "0-6"
    return f"🚨 *Ball delivered* 🌀\n👉 {escape_md(striker_name)}, type {range_text} to hit! 🏏🔴"


def _player_status_icon(match: MatchState, team, player) -> str:
    if player.is_out:
        return "❌"
    if player.user_id == match.striker_id:
        return "🎯"
    if match.bowler_id and player.user_id == match.bowler_id:
        return "⚡"
    return "⏳"


def _format_team_block(match: MatchState, team) -> list:
    is_batting = team.key == match.batting_team_key
    overs_display = f"{match.current_over}.{match.current_ball}ov" if is_batting else None

    if is_batting:
        rr = match.run_rate()
        header = f"🔴 TEAM {team.key.value} | {match.score}/{match.wickets}"
        sub = f"├ {overs_display} | RR {rr}"
    else:
        header = f"🔵 TEAM {team.key.value} | -"
        sub = None

    lines = [header]
    if sub:
        lines.append(sub)

    for p in team.players.values():
        icon = _player_status_icon(match, team, p)
        lines.append("")
        lines.append(f"👤 {escape_md(p.display_name)} {icon}")

        sr = round((p.runs / p.balls_faced) * 100) if p.balls_faced else 0
        lines.append(f"├ 🏏 {p.runs}({p.balls_faced}) | SR {sr}")

        if p.balls_bowled:
            bowl_overs = p.balls_bowled // 6
            bowl_balls = p.balls_bowled % 6
            eco = round(p.runs_conceded / (p.balls_bowled / 6), 1) if p.balls_bowled else 0.0
            lines.append(
                f"├ 🥎 {p.wickets_taken}W | {p.runs_conceded}R | {bowl_overs}.{bowl_balls}ov | Eco {eco}"
            )
            if p.spells:
                for i, spell in enumerate(p.spells, start=1):
                    sp_overs = spell["balls"] // 6
                    sp_balls = spell["balls"] % 6
                    prefix = "└" if i == len(p.spells) else "├"
                    label = "Spell" if i == 1 and len(p.spells) == 1 else f"Spell{i}"
                    lines.append(
                        f"   └ {label} | {sp_overs}.{sp_balls}ov | {spell['runs']}R | {spell['wickets']}W"
                    )
            else:
                lines.append("└ 📋 No spell data")
        else:
            lines.append("└ 🥎 No bowling data")

    return lines


def format_team_scorecard(match: MatchState) -> str:
    """The tree-style /score scorecard for a live Team Match -- shown to
    anyone in the chat any time, mirroring the /soloscore style but split
    across both teams, plus a chase/target block once the 2nd innings is
    underway."""
    lines = [
        "🌀━━━━━━━━",
        "⚡TEAM SCORECARD",
        "           ━━━━━━━━🌀",
        "",
    ]

    if match.target is not None:
        remaining = max(match.target - match.score, 0)
        balls_left = max(match.balls_remaining(), 0)
        req_rr = match.required_run_rate()
        lines.append(f"🎯 Chase | Target {match.target}")
        lines.append(f"📉 Need {remaining} in {balls_left} balls")
        if req_rr is not None:
            lines.append(f"⚡ RRR | {req_rr}")
        lines.append("")

    lines.append("✦ ─────────────────── ✦")
    batting_team = match.batting_team
    bowling_team = match.bowling_team

    lines.extend(_format_team_block(match, batting_team))
    lines.append("")
    lines.append("✦ ─────────────────── ✦")
    lines.extend(_format_team_block(match, bowling_team))
    lines.append("")
    lines.append("#cricketroyale")

    return "\n".join(lines)


def format_innings_summary(match: MatchState, team: "TeamState") -> str:  # type: ignore[name-defined]
    lines = [f"📋 *{escape_md(team.name)} Innings Summary* 📋", DIVIDER]
    for p in team.players.values():
        if p.balls_faced > 0 or p.is_out:
            status = "out" if p.is_out else "not out"
            lines.append(f"🏏 {escape_md(p.display_name)}: {p.runs} ({p.balls_faced}b) — {status}")
    for p in team.players.values():
        if p.balls_bowled > 0:
            overs_bowled = p.balls_bowled // 6
            balls_part = p.balls_bowled % 6
            lines.append(
                f"🥎 {escape_md(p.display_name)} (bowling): {overs_bowled}.{balls_part}-"
                f"{p.runs_conceded}-{p.wickets_taken}"
            )
    return "\n".join(lines)
