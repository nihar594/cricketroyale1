# 🏏 Cricket Royale

A production-ready, multiplayer Telegram cricket bot built with
**python-telegram-bot v21+**, **asyncio**, and **SQLite**. Play a live
Team Match with friends in a group chat, or a quick multiplayer Solo
Match (Royale mode) where everyone bats in join order.

---

## Features

- **Solo Match (Royale mode)** — multiplayer, no toss. Whoever joins
  first bats first; everyone else bowls, rotating one over at a time in
  join order. When the batter is out, the next joiner bats next, and
  the batter who's out joins the bowling rotation. Continues until
  everyone has had a turn; highest score wins.
- **Team Match** — live-edited lobby, Team A vs Team B, 2-minute join
  timer, auto-start once both teams have 2+ players, auto-cancel
  otherwise.
- **Captains** — only a team's own members can become that team's
  captain.
- **Toss** — GIF-backed coin toss, winner picks Bat or Bowl.
- **Overs** — pick any match length from 1 to 20 overs.
- **Gameplay** — batting captain sets the striker/non-striker with
  `/batting`; bowling captain sets the bowler with `/bowling`. The
  bowler taps **Bowl Now**, gets DM'd by the bot, and types `1-6`
  privately. The striker then types `0-6` in the group. Matching
  numbers = wicket; otherwise runs = the batter's number. Strike
  rotates on 1, 3, and 5 — just like real cricket.
- **Live scoreboard** — score, wickets, overs, run rate, target,
  required run rate, striker/non-striker, current bowler, and a
  ball-by-ball view of the current over, refreshed after every ball.
- **Commentary** — short random English commentary generated for
  every single ball.
- **Leaderboard** — persistent SQLite-backed player stats: matches
  played/won, runs scored, wickets taken.
- **Career stats (`/userstats`)** — a full stats card per player: highest
  score, batting average, strike rate, 4s/6s, 50s/100s, ducks, wickets,
  economy, solo vs team matches played, MOTM awards, and a simple
  EXP/level system (Rookie → Pro → Elite → Legendary → Mythic).
- **Gems economy (`/gems`)** — every player starts with 5,000 Gems. After
  every match, the winning side gets +200 Gems each and the Player of the
  Match gets an extra +100 Gems bonus (both configurable in `config.py`
  via `WINNER_GEMS_REWARD` / `POTM_GEMS_REWARD`). The bot owner always
  has an effectively unlimited balance.
- **Player of the Match** — at the end of every match, the standout
  performer's Telegram profile photo is automatically composited into
  an award banner and posted to the chat.
- **Response timeouts (Solo Match only)** — the current bowler or
  batter gets 60 seconds to respond, with reminders at 50s and 30s
  left (tagging them directly). If they still haven't responded by
  60s, they're removed from the match entirely (never bats or bowls
  again) and the match continues with whoever's left.
- **GIFs** — dedicated GIFs for 0, 1, 2, 3, 4, 5, 6, wicket, toss, and
  the welcome banner.

---

## Project layout

```
cricket_royale/
├── main.py                # entrypoint, wires up all handlers
├── config.py               # all tunables & asset paths
├── database.py               # async SQLite persistence (users/leaderboard)
├── requirements.txt
├── README.md
├── handlers/
│   ├── start.py               # /start, main menu
│   ├── lobby.py                 # team lobby, join/leave, timeout, auto-start
│   ├── captains.py                # captain selection
│   ├── toss.py                      # coin toss + bat/bowl decision (team mode)
│   ├── overs.py                       # overs selection (both modes)
│   ├── solo.py                          # Solo Match (Royale mode) join-order rotation
│   ├── batting.py                         # /batting striker & non-striker selection
│   ├── bowling.py                           # /bowling bowler selection
│   ├── gameplay.py                            # ball-by-ball engine, DM/group input routing
│   ├── teams.py                                 # /teams, /add, /remove
│   ├── leaderboard.py                             # /leaderboard
│   └── commands.py                                  # /endmatch + global error handler
├── utils/
│   ├── models.py              # in-memory MatchState / TeamState / Player
│   ├── keyboards.py             # all InlineKeyboardMarkup builders
│   ├── commentary.py              # random ball-by-ball commentary
│   ├── scoreboard.py                # live scoreboard text formatting
│   └── media.py                       # safe GIF/photo sending with fallback
└── assets/
    ├── banner.jpg               # shown on /start
    └── gifs/
        ├── welcome.gif
        ├── toss.gif
        ├── wicket.gif
        └── 0.gif ... 6.gif        # one GIF per possible run outcome
```

---

## Owner setup

To unlock the owner-only commands (`/broadcast`, `/botstats`, `/maintenance`,
`/forceend`, `/ownerhelp`), set your personal numeric Telegram user ID —
get it by messaging **@userinfobot** on Telegram — either as an environment
variable:

```bash
export CRICKET_ROYALE_OWNER_ID="123456789"
```

or by editing `OWNER_ID` directly in `config.py`. These commands are
silently ignored for anyone else -- they don't even reveal that they exist.

## Setup

> ⚠️ **Don't lose your data on updates!** Every player's stats, gems, and
> ban list live in one file: `cricket_royale.db`. If you replace the whole
> project folder when you get an updated zip, you'll delete this file and
> everyone's progress resets to zero. See "Keeping your data safe" below
> — it takes 30 seconds to avoid this permanently.

1. **Install dependencies** (Python 3.12+ recommended):

   ```bash
   pip install -r requirements.txt
   ```

2. **Add your bot token.** Either set an environment variable:

   ```bash
   export CRICKET_ROYALE_BOT_TOKEN="123456:ABC-your-real-token"
   ```

   or edit `BOT_TOKEN` directly in `config.py`.

3. **Drop in your media assets** using these *exact* filenames:

   | File                      | Used for                   |
   |---------------------------|-----------------------------|
   | `assets/banner.jpg`       | `/start` welcome banner      |
   | `assets/team_match_banner.jpg` | Shown when "Team Match" is selected |
   | `assets/player_of_match_banner.jpg` | Award template -- the winner's profile photo is composited into its circular frame |
   | `assets/gifs/welcome.gif` | Available for custom use      |
   | `assets/gifs/toss.gif`    | Coin toss                      |
   | `assets/gifs/wicket.gif`  | Every wicket                    |
   | `assets/gifs/0.gif`       | Dot ball                         |
   | `assets/gifs/1.gif`       | Single                             |
   | `assets/gifs/2.gif`       | Two runs                            |
   | `assets/gifs/3.gif`       | Three runs                           |
   | `assets/gifs/4.gif`       | Four                                   |
   | `assets/gifs/5.gif`       | Five runs                               |
   | `assets/gifs/6.gif`       | Six                                       |
   | `assets/gifs/bowling.gif` | Shown with the "Bowl Now" status prompt    |
   | `assets/gifs/ball_delivered.gif` | Shown asking the batter to type 0-6 |
   | `assets/gifs/fifty.gif`   | Shown when a batter reaches 50 runs        |
   | `assets/gifs/century.gif` | Shown when a batter reaches 100 runs       |
   | `assets/gifs/batting.gif` | **Owner-only** -- plays only when the bot owner personally comes out to bat |
   | `assets/userstats_template.jpg` | `/userstats` card template -- profile photo + live stats composited onto it |
   | `assets/fonts/DejaVuSans-Bold.ttf` | Bundled font used to draw stat numbers onto the userstats card (already included) |

   If a file is missing, the bot gracefully falls back to a plain text
   message instead of crashing — so you can run it right away and add
   real media later.

4. **Run the bot:**

   ```bash
   python main.py
   ```

5. Add the bot to a group chat (for Team Match) and/or message it
   directly (for Solo Match).

---

## Keeping your data safe (read this before your next update)

All player data -- stats, gems, ban list, everything -- lives in a single
SQLite file: `cricket_royale.db`. By default it's created right inside
the project folder. **If you ever delete the whole folder to install an
updated zip, you delete this file too, and every player's progress goes
back to zero.**

Fix it once, permanently, by pointing the database at a location outside
the project folder:

```bash
export CRICKET_ROYALE_DB_PATH="/home/youruser/cricket_royale_data/cricket_royale.db"
```

(On Windows: `set CRICKET_ROYALE_DB_PATH=C:\CricketRoyaleData\cricket_royale.db`,
or set it permanently via System Properties → Environment Variables.)

The folder will be created automatically the first time the bot runs. From
then on, you can delete and replace the `cricket_royale` project folder as
often as you like when installing updates -- your data lives elsewhere and
is never touched.

**Already lost data from an update, or don't want to set an env var?**
Just copy `cricket_royale.db` out of your old project folder *before*
deleting it, then copy it back into the new folder (same spot, right next
to `main.py`) after extracting the new zip, before you restart the bot.

---

## Commands

| Command        | Description                                                       |
|-----------------|---------------------------------------------------------------------|
| `/start`        | Shows the banner + Solo Match / Team Match / Leaderboard / Cancel    |
| `/teams`        | Shows current lobby/match rosters and captains                        |
| `/create_team`  | Host-only: opens team registration after claiming Host                 |
| `/rejoin`       | Host-only: extends team registration by 30 seconds                      |
| `/leaderboard`  | Top 10 players by wins, runs, wickets                                  |
| `/batting [n]`  | Batting captain or Host: no number shows a numbered list of batters; with a number, selects that batter (run twice for striker + non-striker) |
| `/bowling [n]`  | Bowling captain or Host: no number shows a numbered list of bowlers (with overs bowled + availability); with a number, selects that bowler |
| `/spamfree`     | Host-only: within 15s of overs being set, activates spam-free mode (no bowler can bowl the same number 3 times in a row) |
| `/add a`        | Join Team A during the lobby phase                                         |
| `/add b`        | Join Team B during the lobby phase                                          |
| `/remove`       | Leave your team during the lobby phase                                       |
| `/endmatch`     | Force-end the current match/lobby (creator, captains, or solo player)          |
| `/join`         | Join an open Solo Match (Royale) queue                                          |
| `/leavesolo`    | Leave the Solo Match queue before it starts                                      |
| `/startsolo`    | Force-start the Solo Match queue early (only whoever ran /start Solo Match)       |
| `/soloscore`    | Anyone can run this to see a live tree-style scorecard for the current Solo Match  |
| `/checkassets`  | Debug: lists every media file the bot looks for and whether it's currently found   |
| `/userstats`    | Visual career stats card with your profile photo (reply to someone's message to check theirs) |
| `/gems`         | Shows your Gems balance                                                             |
| `/give`         | Send Gems to another player -- reply to their message with `/give <amount>`, or tag them: `/give <amount> @username` |
| `/flip`         | Coin-flip gambling: `/flip h <amount>` or `/flip t <amount>` (45% win chance)         |
| `/dice`         | Dice-roll gambling: `/dice <amount>` -- rolls 1-6, 4/5/6 wins, 1/2/3 loses (45% win chance) |
| `/ownerhelp`    | 👑 Owner-only: lists owner commands (silently ignored for everyone else)             |
| `/broadcast`    | 👑 Owner-only: send an announcement to every chat the bot has seen                    |
| `/botstats`     | 👑 Owner-only: global bot usage stats                                                   |
| `/maintenance`  | 👑 Owner-only: `on`/`off` -- blocks new matches for everyone but you                     |
| `/forceend`     | 👑 Owner-only: force-end the match/lobby in any chat by chat_id                           |
| `/banuser`      | 👑 Owner-only: reply to someone's message to ban them from the bot                          |
| `/unbanuser`    | 👑 Owner-only: reply to someone's message to unban them                                      |
| `/banlist`      | 👑 Owner-only: lists everyone currently banned                                                |

---

## How a Team Match plays out

1. Someone taps **Team Match** from `/start` in a group chat.
2. Whoever taps **🎪 Main Host Banunga** first becomes the Host of the match,
   and team registration opens automatically right away.
3. The bot posts a live registration message with **Join Team A** / **Join
   Team B** / **Leave Lobby** / **Force Start** buttons. It's edited in
   place every time someone joins or leaves.
5. A timer runs (default 2 minutes). If both teams reach 2+ players before
   it expires, the match starts immediately. The Host can also tap
   **⚡ Force Start** to skip the wait once the minimum is met, or send
   `/rejoin` to extend the timer by another 30 seconds. If time runs out
   without enough players, the lobby is cancelled automatically.
6. Each team picks its own captain (only members of that team can
   claim the captaincy).
7. The bot plays the toss GIF and randomly picks a winning captain,
   who chooses to **Bat** or **Bowl** first.
8. The toss-winning captain picks how many overs (1-20).
9. The batting captain runs `/batting` to send in the openers; the
   bowling captain runs `/bowling` to pick the first bowler.
10. Every delivery: the bowler taps **Bowl Now**, is DM'd by the bot,
    and privately types a number `1-6`. The striker then types a
    number `0-6` in the group. Same number = wicket; otherwise the
    batter scores that many runs. The bot posts the matching GIF,
    fresh commentary, and an updated live scoreboard after every ball.
11. After 6 balls the over ends and the bowling captain must pick a
    new bowler (the previous over's bowler can't bowl the next one).
   After a wicket, the batting captain sends in the next batter.
10. When the first innings ends (all out or overs complete), the
    chase begins with a target. The match ends the moment the target
    is reached, the overs run out, or the side is bowled out —
    whichever comes first. Final stats are saved to the leaderboard
    automatically.

Solo Match (Royale mode) uses its own join-based lobby (same 2-minute
timer, minimum 2 players) — no toss, no captains, no overs limit. The
bowling rotation and next-batter selection are fully automatic.

---

## Architecture notes

- **Live match state lives in memory** (`utils/models.MatchStore`), one
  `MatchState` per active chat. This keeps ball-by-ball gameplay fast
  and simple. Only the final result of each match is persisted to
  SQLite, which is all that's needed to power `/leaderboard`.
- Restarting the bot process clears any matches that were in progress
  at the time (by design, to keep the in-memory model simple and
  fast). Completed-match stats already saved to SQLite are never lost.
- Every GIF/photo send goes through `utils/media.py`, which falls
  back to plain text if the asset file isn't present yet, so the bot
  never crashes due to missing media.
