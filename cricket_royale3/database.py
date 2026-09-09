"""
Cricket Royale - Persistence layer.

Only durable, cross-match data lives here: registered users and their
career statistics (used for /leaderboard). Live match state is kept in
memory -- see utils/models.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

import aiosqlite

import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT NOT NULL,
    matches_played  INTEGER NOT NULL DEFAULT 0,
    matches_won     INTEGER NOT NULL DEFAULT 0,
    runs_scored     INTEGER NOT NULL DEFAULT 0,
    balls_faced     INTEGER NOT NULL DEFAULT 0,
    highest_score   INTEGER NOT NULL DEFAULT 0,
    highest_score_balls INTEGER NOT NULL DEFAULT 0,
    wickets_taken   INTEGER NOT NULL DEFAULT 0,
    balls_bowled    INTEGER NOT NULL DEFAULT 0,
    runs_conceded   INTEGER NOT NULL DEFAULT 0,
    best_bowling    INTEGER NOT NULL DEFAULT 0,
    fours           INTEGER NOT NULL DEFAULT 0,
    sixes           INTEGER NOT NULL DEFAULT 0,
    fifties         INTEGER NOT NULL DEFAULT 0,
    centuries       INTEGER NOT NULL DEFAULT 0,
    ducks           INTEGER NOT NULL DEFAULT 0,
    times_out       INTEGER NOT NULL DEFAULT 0,
    solo_matches    INTEGER NOT NULL DEFAULT 0,
    team_matches    INTEGER NOT NULL DEFAULT 0,
    motm_awards     INTEGER NOT NULL DEFAULT 0,
    gems            INTEGER NOT NULL DEFAULT 5000,
    is_banned       INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS matches (
    match_id        TEXT PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    mode            TEXT NOT NULL,
    overs           INTEGER,
    winner          TEXT,
    team_a_score    INTEGER,
    team_b_score    INTEGER,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    ended_at        TEXT
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id         INTEGER PRIMARY KEY,
    chat_type       TEXT,
    title           TEXT,
    last_seen       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_cache (
    asset_path      TEXT PRIMARY KEY,
    file_id         TEXT NOT NULL,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added after the original release -- kept here so upgrading an
# existing database (created before these existed) adds them via ALTER
# TABLE instead of requiring users to delete their .db file.
_USER_COLUMNS_V2 = {
    "highest_score_balls": "INTEGER NOT NULL DEFAULT 0",
    "fours": "INTEGER NOT NULL DEFAULT 0",
    "sixes": "INTEGER NOT NULL DEFAULT 0",
    "fifties": "INTEGER NOT NULL DEFAULT 0",
    "centuries": "INTEGER NOT NULL DEFAULT 0",
    "ducks": "INTEGER NOT NULL DEFAULT 0",
    "times_out": "INTEGER NOT NULL DEFAULT 0",
    "solo_matches": "INTEGER NOT NULL DEFAULT 0",
    "team_matches": "INTEGER NOT NULL DEFAULT 0",
    "motm_awards": "INTEGER NOT NULL DEFAULT 0",
    "gems": "INTEGER NOT NULL DEFAULT 5000",
    "is_banned": "INTEGER NOT NULL DEFAULT 0",
}


class Database:
    """Thin async wrapper around aiosqlite for Cricket Royale's needs."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        # WAL lets reads and writes proceed concurrently instead of the
        # default journal mode's whole-file lock on every write -- with
        # many chats hitting the DB at once (stats, gems, chat upserts,
        # the new media_cache lookups), that lock was a real source of
        # per-command lag. NORMAL sync is the standard safe pairing with
        # WAL, and busy_timeout avoids "database is locked" errors instead
        # of failing immediately during brief contention.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()
        await self._ensure_owner_gems()
        logger.info("Database ready at %s", self.db_path)

    async def _ensure_owner_gems(self) -> None:
        """Guarantees the configured owner always has at least
        config.OWNER_STARTING_GEMS -- runs once per connect, never lowers
        their balance if it's already higher."""
        if not config.OWNER_ID:
            return
        await self._conn.execute(
            """
            INSERT INTO users (user_id, first_name, gems)
            VALUES (?, 'Owner', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                gems = MAX(gems, excluded.gems)
            """,
            (config.OWNER_ID, config.OWNER_STARTING_GEMS),
        )
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Adds any columns introduced after the original release to an
        existing users table, so upgrading never requires deleting the
        database file."""
        cursor = await self._conn.execute("PRAGMA table_info(users)")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for column, decl in _USER_COLUMNS_V2.items():
            if column not in existing:
                await self._conn.execute(f"ALTER TABLE users ADD COLUMN {column} {decl}")
                logger.info("Migrated users table: added column %s", column)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected yet. Call connect() first.")
        return self._conn

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    async def upsert_user(self, user_id: int, username: Optional[str], first_name: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user_id, username, first_name),
        )
        await self.conn.commit()

    async def record_player_result(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
        runs: int,
        balls_faced: int,
        wickets: int,
        balls_bowled: int,
        runs_conceded: int,
        won: bool,
        fours: int = 0,
        sixes: int = 0,
        is_fifty: bool = False,
        is_century: bool = False,
        is_duck: bool = False,
        was_out: bool = False,
        is_solo: bool = False,
        is_motm: bool = False,
    ) -> None:
        await self.upsert_user(user_id, username, first_name)
        await self.conn.execute(
            """
            UPDATE users SET
                matches_played = matches_played + 1,
                matches_won = matches_won + ?,
                runs_scored = runs_scored + ?,
                balls_faced = balls_faced + ?,
                highest_score = MAX(highest_score, ?),
                highest_score_balls = CASE WHEN ? > highest_score THEN ? ELSE highest_score_balls END,
                wickets_taken = wickets_taken + ?,
                balls_bowled = balls_bowled + ?,
                runs_conceded = runs_conceded + ?,
                best_bowling = MAX(best_bowling, ?),
                fours = fours + ?,
                sixes = sixes + ?,
                fifties = fifties + ?,
                centuries = centuries + ?,
                ducks = ducks + ?,
                times_out = times_out + ?,
                solo_matches = solo_matches + ?,
                team_matches = team_matches + ?,
                motm_awards = motm_awards + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (
                1 if won else 0,
                runs,
                balls_faced,
                runs,
                runs, balls_faced,
                wickets,
                balls_bowled,
                runs_conceded,
                wickets,
                fours,
                sixes,
                1 if is_fifty else 0,
                1 if is_century else 0,
                1 if is_duck else 0,
                1 if was_out else 0,
                1 if is_solo else 0,
                0 if is_solo else 1,
                1 if is_motm else 0,
                user_id,
            ),
        )
        await self.conn.commit()

    async def get_user_stats(self, user_id: int) -> Optional[aiosqlite.Row]:
        cursor = await self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_user_by_username(self, username: str) -> Optional[aiosqlite.Row]:
        """Case-insensitive lookup by stored Telegram username (without the
        leading @). Only finds users who have interacted with the bot
        before -- Telegram doesn't resolve @mentions to an ID for us."""
        cursor = await self.conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username.lstrip("@"),)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def add_gems(
        self, user_id: int, amount: int, username: Optional[str] = None, first_name: str = ""
    ) -> None:
        """Adds (or subtracts, if amount is negative) gems for a user,
        creating their row first if it doesn't exist yet."""
        await self.upsert_user(user_id, username, first_name or "Player")
        await self.conn.execute(
            "UPDATE users SET gems = gems + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (amount, user_id),
        )
        await self.conn.commit()

    async def get_gems(self, user_id: int) -> int:
        cursor = await self.conn.execute("SELECT gems FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row["gems"] if row else 0

    async def get_leaderboard(self, limit: int = 10) -> Sequence[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, username, first_name, matches_played, matches_won,
                   runs_scored, wickets_taken
            FROM users
            WHERE matches_played > 0
            ORDER BY matches_won DESC, runs_scored DESC, wickets_taken DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    # Column each ranking category sorts by, plus a minimum-activity guard
    # so brand-new/inactive accounts don't clutter a category they haven't
    # actually done anything in yet.
    RANKING_CATEGORIES = {
        "ducks": ("ducks", "times_out > 0"),
        "sixes": ("sixes", "matches_played > 0"),
        "wickets": ("wickets_taken", "matches_played > 0"),
        "runs": ("runs_scored", "matches_played > 0"),
        "fifties": ("fifties", "matches_played > 0"),
        "centuries": ("centuries", "matches_played > 0"),
        "highest_score": ("highest_score", "matches_played > 0"),
        "gems": ("gems", "1=1"),
    }

    async def get_ranking(self, category: str, limit: int = 10, offset: int = 0) -> Sequence[aiosqlite.Row]:
        """Paginated Top-N ranking for any of RANKING_CATEGORIES. Ties are
        broken by user_id for a stable order -- otherwise Back/Next would
        reshuffle tied players between page loads."""
        column, guard = self.RANKING_CATEGORIES[category]
        extra = ", highest_score_balls" if category == "highest_score" else ""
        cursor = await self.conn.execute(
            f"""
            SELECT user_id, username, first_name, {column} AS value, balls_faced{extra}
            FROM users
            WHERE {guard}
            ORDER BY {column} DESC, user_id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return rows

    async def get_rank_of(self, category: str, user_id: int) -> Optional[dict]:
        """This player's own position in a ranking category, even if
        they're outside the Top N shown on the page."""
        column, guard = self.RANKING_CATEGORIES[category]
        row = await self.get_user_stats(user_id)
        if row is None:
            return None
        # Count how many players strictly outrank them on this column.
        cursor = await self.conn.execute(
            f"SELECT COUNT(*) AS n FROM users WHERE {guard} AND {column} > ?",
            (row[column],),
        )
        better = await cursor.fetchone()
        await cursor.close()
        return {"rank": better["n"] + 1, "value": row[column], "balls_faced": row["balls_faced"]}

    async def get_total_ranked_players(self, category: str) -> int:
        _, guard = self.RANKING_CATEGORIES[category]
        cursor = await self.conn.execute(f"SELECT COUNT(*) AS n FROM users WHERE {guard}")
        row = await cursor.fetchone()
        await cursor.close()
        return row["n"]

    # ------------------------------------------------------------------
    # Matches (summary log)
    # ------------------------------------------------------------------
    async def log_match(
        self,
        match_id: str,
        chat_id: int,
        mode: str,
        overs: Optional[int],
        winner: Optional[str],
        team_a_score: Optional[int],
        team_b_score: Optional[int],
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO matches (match_id, chat_id, mode, overs, winner,
                                  team_a_score, team_b_score, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(match_id) DO UPDATE SET
                winner = excluded.winner,
                team_a_score = excluded.team_a_score,
                team_b_score = excluded.team_b_score,
                ended_at = CURRENT_TIMESTAMP
            """,
            (match_id, chat_id, mode, overs, winner, team_a_score, team_b_score),
        )
        await self.conn.commit()

    # ------------------------------------------------------------------
    # Chats (for /broadcast) and owner-facing bot-wide stats
    # ------------------------------------------------------------------
    async def upsert_chat(self, chat_id: int, chat_type: str, title: Optional[str]) -> None:
        await self.conn.execute(
            """
            INSERT INTO chats (chat_id, chat_type, title, last_seen)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_type = excluded.chat_type,
                title = excluded.title,
                last_seen = CURRENT_TIMESTAMP
            """,
            (chat_id, chat_type, title),
        )
        await self.conn.commit()

    async def get_cached_file_id(self, asset_path: str) -> Optional[str]:
        """Telegram file_id for a given local asset path, if we've ever
        uploaded it before -- persisted so a Railway/host restart doesn't
        force every GIF/photo to be slowly re-uploaded from scratch again."""
        cursor = await self.conn.execute(
            "SELECT file_id FROM media_cache WHERE asset_path = ?", (asset_path,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row["file_id"] if row else None

    async def set_cached_file_id(self, asset_path: str, file_id: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO media_cache (asset_path, file_id, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(asset_path) DO UPDATE SET
                file_id = excluded.file_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (asset_path, file_id),
        )
        await self.conn.commit()

    async def clear_cached_file_id(self, asset_path: str) -> None:
        await self.conn.execute("DELETE FROM media_cache WHERE asset_path = ?", (asset_path,))
        await self.conn.commit()

    async def get_all_chat_ids(self) -> List[int]:
        cursor = await self.conn.execute("SELECT chat_id FROM chats")
        rows = await cursor.fetchall()
        await cursor.close()
        return [row["chat_id"] for row in rows]

    async def get_bot_stats(self) -> dict:
        cursor = await self.conn.execute("SELECT COUNT(*) AS n FROM users")
        total_users = (await cursor.fetchone())["n"]
        await cursor.close()

        cursor = await self.conn.execute("SELECT COUNT(*) AS n FROM chats")
        total_chats = (await cursor.fetchone())["n"]
        await cursor.close()

        cursor = await self.conn.execute("SELECT COUNT(*) AS n FROM matches")
        total_matches = (await cursor.fetchone())["n"]
        await cursor.close()

        cursor = await self.conn.execute(
            "SELECT COALESCE(SUM(runs_scored), 0) AS n FROM users"
        )
        total_runs = (await cursor.fetchone())["n"]
        await cursor.close()

        cursor = await self.conn.execute(
            "SELECT COALESCE(SUM(wickets_taken), 0) AS n FROM users"
        )
        total_wickets = (await cursor.fetchone())["n"]
        await cursor.close()

        return {
            "total_users": total_users,
            "total_chats": total_chats,
            "total_matches": total_matches,
            "total_runs": total_runs,
            "total_wickets": total_wickets,
        }

    # ------------------------------------------------------------------
    # Ban management (owner-only tools)
    # ------------------------------------------------------------------
    async def ban_user(self, user_id: int, username: Optional[str], first_name: str) -> None:
        await self.upsert_user(user_id, username, first_name)
        await self.conn.execute(
            "UPDATE users SET is_banned = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,),
        )
        await self.conn.commit()

    async def unban_user(self, user_id: int) -> None:
        await self.conn.execute(
            "UPDATE users SET is_banned = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,),
        )
        await self.conn.commit()

    async def is_user_banned(self, user_id: int) -> bool:
        cursor = await self.conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return bool(row["is_banned"]) if row else False

    async def get_banned_users(self) -> List[aiosqlite.Row]:
        cursor = await self.conn.execute(
            "SELECT user_id, username, first_name FROM users WHERE is_banned = 1 ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return rows
