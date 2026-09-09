"""
Career stats card formatting for /userstats, including a simple EXP and
level system derived from a player's cumulative career numbers.
"""

from __future__ import annotations

from typing import Optional, Tuple

# (EXP threshold, level label) -- must stay sorted ascending by threshold.
LEVELS = [
    (0, "Rookie 🌱"),
    (500, "Pro ⚡"),
    (2000, "Elite 🔥"),
    (5000, "Legendary 🌟"),
    (10000, "Mythic 👑"),
]


def compute_exp(row) -> int:
    """A simple, transparent EXP formula built entirely from tracked
    career stats: runs, wickets, match wins, and Player of the Match
    awards all contribute."""
    return (
        row["runs_scored"]
        + row["wickets_taken"] * 20
        + row["matches_won"] * 10
        + row["motm_awards"] * 50
    )


def level_progress(exp: int) -> Tuple[str, Optional[str], int]:
    """Returns (current_level_label, next_level_label_or_None, exp_needed_for_next)."""
    current_label = LEVELS[0][1]
    for threshold, label in LEVELS:
        if exp >= threshold:
            current_label = label
        else:
            break

    for threshold, label in LEVELS:
        if exp < threshold:
            return current_label, label, threshold - exp

    return current_label, None, 0


def format_user_stats(row, display_name: str, gems: int = 0) -> str:
    exp = compute_exp(row)
    current_level, next_level, needed = level_progress(exp)

    if next_level:
        level_line = f"⭐ EXP: {exp} | Next: {next_level} (Need {needed} more EXP)"
    else:
        level_line = f"⭐ EXP: {exp} | Max level reached! 👑"

    strike_rate = round((row["runs_scored"] / row["balls_faced"]) * 100, 2) if row["balls_faced"] else 0.0
    batting_avg = (
        round(row["runs_scored"] / row["times_out"], 2) if row["times_out"] else None
    )
    avg_display = f"{batting_avg}" if batting_avg is not None else "N/A (never out)"

    overs_bowled = row["balls_bowled"] // 6
    balls_part = row["balls_bowled"] % 6
    economy = (
        round(row["runs_conceded"] / (row["balls_bowled"] / 6), 2) if row["balls_bowled"] else 0.0
    )

    highest_score_line = f"{row['highest_score']}"
    if row["highest_score_balls"]:
        highest_score_line += f" ({row['highest_score_balls']})"

    lines = [
        f"🚀 {current_level} *STATISTICS*",
        f"━━━━━━━━━━━━━━━━━━",
        f"👤 Name: {display_name}",
        f"🆔 ID: {row['user_id']}",
        level_line,
        "",
        "🏏 *BATTING STATS*",
        f"🌀 Highest Score: {highest_score_line}",
        f"👀 Total Runs: {row['runs_scored']}",
        f"🎀 Batting Avg: {avg_display}",
        f"⚡ Strike Rate: {strike_rate}",
        f"💥 6s: {row['sixes']} | 4s: {row['fours']}",
        f"🕸️ 100s: {row['centuries']} | 50s: {row['fifties']}",
        f"🔸 Ducks 🦆: {row['ducks']}",
        "",
        "🥎 *BOWLING STATS*",
        f"👾 Wickets: {row['wickets_taken']}",
        f"🌪️ Hat-Tricks: 0",
        f"🧤 Catches: 0",
        f"🍁 Overs Bowled: {overs_bowled}.{balls_part}",
        f"💐 Economy: {economy}",
        "",
        "🏆 *MATCH & AWARDS*",
        f"⛄ Solo Matches: {row['solo_matches']}",
        f"☃️ Team Matches: {row['team_matches']}",
        f"🎉 MOTM Awards: {row['motm_awards']}",
        f"🪙 Gems: {gems}",
        "",
        "@cricketroyale #cricketroyale",
    ]
    return "\n".join(lines)
