"""
In-memory data models for a live Cricket Royale match.

A match is a fast-moving, real-time object -- storing every ball to SQLite
would be wasteful and slow. Instead we keep the *live* state of a match in
memory (one MatchState per active chat) and only persist the final,
aggregated result of each player to the database once the match ends.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional


class MatchMode(Enum):
    SOLO = "solo"
    TEAM = "team"


class MatchStatus(Enum):
    AWAITING_HOST = auto()
    AWAITING_CREATE_TEAM = auto()
    LOBBY = auto()
    CAPTAIN_SELECT = auto()
    TOSS = auto()
    OVERS_SELECT = auto()
    SPAM_FREE_DECISION = auto()
    AWAITING_LINEUP = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    CANCELLED = auto()


class TeamKey(Enum):
    A = "A"
    B = "B"

    @property
    def other(self) -> "TeamKey":
        return TeamKey.B if self is TeamKey.A else TeamKey.A


@dataclass
class Player:
    user_id: int
    username: Optional[str]
    first_name: str

    runs: int = 0
    balls_faced: int = 0
    fours: int = 0
    sixes: int = 0
    is_out: bool = False

    balls_bowled: int = 0
    runs_conceded: int = 0
    wickets_taken: int = 0

    # Per-over bowling "spells" -- each dict is {"balls": int, "runs": int,
    # "wickets": int} for one completed over. current_spell_* accumulate
    # the in-progress over and get flushed into `spells` when it ends.
    spells: list = field(default_factory=list)
    current_spell_balls: int = 0
    current_spell_runs: int = 0
    current_spell_wickets: int = 0

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name


@dataclass
class TeamState:
    key: TeamKey
    name: str
    players: Dict[int, Player] = field(default_factory=dict)
    captain_id: Optional[int] = None

    def add_player(self, player: Player) -> None:
        self.players[player.user_id] = player

    def remove_player(self, user_id: int) -> None:
        self.players.pop(user_id, None)
        if self.captain_id == user_id:
            self.captain_id = None

    @property
    def size(self) -> int:
        return len(self.players)

    @property
    def all_out(self) -> bool:
        # A team is all out when only one player remains who hasn't been
        # dismissed (there is no one left to partner the striker).
        not_out = [p for p in self.players.values() if not p.is_out]
        return len(not_out) <= 1 and self.size > 1

    def roster_lines(self) -> List[str]:
        from utils.scoreboard import escape_md
        lines = []
        for p in self.players.values():
            tag = " 🧢(C)" if p.user_id == self.captain_id else ""
            lines.append(f"• {escape_md(p.display_name)}{tag}")
        return lines or ["  _(empty)_"]


@dataclass
class BallEvent:
    over: int
    ball: int
    bowler_id: int
    batter_id: int
    runs: int
    is_wicket: bool
    commentary: str


@dataclass
class MatchState:
    chat_id: int
    mode: MatchMode
    initiator_id: int  # whoever tapped "Team Match" / started the match
    creator_id: Optional[int] = None  # the claimed Host; None until claimed
    match_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: MatchStatus = MatchStatus.LOBBY
    created_at: float = field(default_factory=time.time)

    team_a: TeamState = field(default_factory=lambda: TeamState(TeamKey.A, "Team A"))
    team_b: TeamState = field(default_factory=lambda: TeamState(TeamKey.B, "Team B"))

    lobby_message_id: Optional[int] = None
    banner_message_id: Optional[int] = None
    lobby_job_name: Optional[str] = None

    overs: int = 0

    toss_winner: Optional[TeamKey] = None
    toss_decision: Optional[str] = None  # "bat" | "bowl"
    toss_caller_key: Optional[TeamKey] = None  # which captain was asked to call heads/tails

    batting_team_key: Optional[TeamKey] = None
    innings: int = 1
    target: Optional[int] = None

    score: int = 0
    wickets: int = 0
    current_over: int = 0
    current_ball: int = 0  # 0-5 within the over

    striker_id: Optional[int] = None
    non_striker_id: Optional[int] = None
    bowler_id: Optional[int] = None
    last_bowler_id: Optional[int] = None  # to prevent bowling consecutive overs

    awaiting_bowler_input: bool = False
    awaiting_batter_input: bool = False
    pending_bowler_number: Optional[int] = None

    # Spam-free mode (Team Match only): once active, a bowler can't send
    # the same delivery number three times in a row. Tracks the last two
    # numbers actually bowled (regardless of who was bowling), and the
    # scheduled job name for the 15s activation window after overs are set.
    spam_free_mode: bool = False
    recent_bowler_numbers: List[int] = field(default_factory=list)
    spamfree_job_name: Optional[str] = None

    this_over_balls: List[str] = field(default_factory=list)
    recent_commentary: List[str] = field(default_factory=list)

    innings_1_summary: Optional[dict] = None

    host_display_name: Optional[str] = None
    chat_username: Optional[str] = None  # public group username, if any -- powers the "Back to Game" DM button

    # Solo Match ("Royale" mode): join-order based, no toss, no AI.
    # royale_order = user_ids in the exact order they joined (= batting order).
    # royale_players = master registry of Player objects (persists stats
    #   across each player's individual turn -- team_a/team_b below just
    #   point at slices of this registry for the *current* mini-innings).
    royale_order: List[int] = field(default_factory=list)
    royale_players: Dict[int, Player] = field(default_factory=dict)
    royale_batter_index: int = 0
    royale_bowling_pool: List[int] = field(default_factory=list)
    royale_bowl_pointer: int = 0
    # Players kicked from a Royale match for not responding in time
    # (see handlers/solo.py's response-timeout system). Once removed, a
    # player never bats or bowls again for the rest of that match.
    royale_removed_players: set = field(default_factory=set)
    bowler_timeout_job_names: List[str] = field(default_factory=list)
    batter_timeout_job_names: List[str] = field(default_factory=list)

    def team(self, key: TeamKey) -> TeamState:
        return self.team_a if key is TeamKey.A else self.team_b

    @property
    def batting_team(self) -> Optional[TeamState]:
        if self.batting_team_key is None:
            return None
        return self.team(self.batting_team_key)

    @property
    def bowling_team(self) -> Optional[TeamState]:
        if self.batting_team_key is None:
            return None
        return self.team(self.batting_team_key.other)

    def find_team_of(self, user_id: int) -> Optional[TeamState]:
        if user_id in self.team_a.players:
            return self.team_a
        if user_id in self.team_b.players:
            return self.team_b
        return None

    def run_rate(self) -> float:
        balls_bowled = self.current_over * 6 + self.current_ball
        if balls_bowled == 0:
            return 0.0
        return round((self.score / balls_bowled) * 6, 2)

    def balls_remaining(self) -> int:
        return self.overs * 6 - (self.current_over * 6 + self.current_ball)

    def required_run_rate(self) -> Optional[float]:
        if self.target is None:
            return None
        remaining_balls = self.balls_remaining()
        if remaining_balls <= 0:
            return None
        remaining_runs = self.target - self.score
        if remaining_runs <= 0:
            return 0.0
        return round((remaining_runs / remaining_balls) * 6, 2)


class MatchStore:
    """Simple registry of active matches, one per chat."""

    def __init__(self) -> None:
        self._matches: Dict[int, MatchState] = {}

    def get(self, chat_id: int) -> Optional[MatchState]:
        return self._matches.get(chat_id)

    def set(self, match: MatchState) -> None:
        self._matches[match.chat_id] = match

    def remove(self, chat_id: int) -> None:
        self._matches.pop(chat_id, None)

    def find_by_pending_bowler(self, user_id: int) -> Optional[MatchState]:
        for match in self._matches.values():
            if match.awaiting_bowler_input and match.bowler_id == user_id:
                return match
        return None

    def all(self) -> List[MatchState]:
        return list(self._matches.values())
