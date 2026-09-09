"""
Cricket Royale - entrypoint.

Wires together every handler and starts long-polling. Run with:

    python main.py

Set your bot token via the CRICKET_ROYALE_BOT_TOKEN environment variable,
or edit config.py directly.
"""

from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

import config
from database import Database
from handlers import batting, bowling, captains, commands, economy, gameplay, leaderboard, lobby, overs, owner, solo, start, teams, toss

logger = logging.getLogger(__name__)


async def _post_init(application: Application) -> None:
    db = Database(config.DB_PATH)
    await db.connect()
    application.bot_data["db"] = db
    bot_user = await application.bot.get_me()
    application.bot_data["bot_username"] = bot_user.username
    logger.info("Cricket Royale is online as @%s", bot_user.username)


async def _post_shutdown(application: Application) -> None:
    db: Database = application.bot_data.get("db")
    if db is not None:
        await db.close()


async def _noop_callback(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


def build_application() -> Application:
    # Default timeouts (a few seconds) are too short for uploading GIF/MP4
    # files -- bump them up so send_animation doesn't time out on larger
    # media, especially over a slower connection.
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=120.0,
        pool_timeout=20.0,
        media_write_timeout=120.0,
    )
    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .request(request)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # ---------------------------------------------------------------- #
    # Commands
    # ---------------------------------------------------------------- #
    application.add_handler(CommandHandler("start", start.start_command))
    application.add_handler(CommandHandler("teams", teams.teams_command))
    application.add_handler(CommandHandler("create_team", lobby.create_team_command))
    application.add_handler(CommandHandler("rejoin", lobby.rejoin_command))
    application.add_handler(CommandHandler("spamfree", overs.spamfree_command))
    application.add_handler(CommandHandler("changeover", overs.changeover_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard.leaderboard_command))
    application.add_handler(CommandHandler("userstats", leaderboard.userstats_command))
    application.add_handler(CallbackQueryHandler(leaderboard.leaderboard_menu_callback, pattern=r"^lb:"))
    application.add_handler(CommandHandler("gems", economy.gems_command))
    application.add_handler(CommandHandler("give", economy.give_command))
    application.add_handler(CommandHandler("flip", economy.flip_command))
    application.add_handler(CommandHandler("dice", economy.dice_command))
    application.add_handler(CommandHandler("batting", batting.batting_command))
    application.add_handler(CommandHandler("bowling", bowling.bowling_command))
    application.add_handler(CommandHandler("add", teams.add_command))
    application.add_handler(CommandHandler("remove", teams.remove_command))
    application.add_handler(CommandHandler("leaveteam", teams.leaveteam_command))
    application.add_handler(CommandHandler("changehost", teams.changehost_command))
    application.add_handler(CommandHandler(["endmatch", "endgame"], commands.endmatch_command))
    application.add_handler(CallbackQueryHandler(commands.endmatch_confirm_callback, pattern=r"^endmatch:confirm$"))
    application.add_handler(CallbackQueryHandler(commands.endmatch_cancel_callback, pattern=r"^endmatch:cancel$"))
    application.add_handler(CommandHandler("checkassets", commands.checkassets_command))
    application.add_handler(CommandHandler("score", commands.score_command))
    application.add_handler(CommandHandler("broadcast", owner.broadcast_command))
    application.add_handler(CommandHandler("botstats", owner.botstats_command))
    application.add_handler(CommandHandler("maintenance", owner.maintenance_command))
    application.add_handler(CommandHandler("forceend", owner.forceend_command))
    application.add_handler(CommandHandler("ownerhelp", owner.ownerhelp_command))
    application.add_handler(CommandHandler("banuser", owner.banuser_command))
    application.add_handler(CommandHandler("unbanuser", owner.unbanuser_command))
    application.add_handler(CommandHandler("banlist", owner.banlist_command))
    application.add_handler(CommandHandler("join", solo.join_command))
    application.add_handler(CommandHandler("leavesolo", solo.leavesolo_command))
    application.add_handler(CommandHandler("startsolo", solo.startsolo_command))
    application.add_handler(CommandHandler("soloscore", solo.soloscore_command))

    # ---------------------------------------------------------------- #
    # Callback queries (inline button taps), routed by callback_data prefix
    # ---------------------------------------------------------------- #
    application.add_handler(CallbackQueryHandler(start.start_menu_callback, pattern=r"^start:"))
    application.add_handler(CallbackQueryHandler(lobby.host_claim_callback, pattern=r"^host:claim$"))
    application.add_handler(CallbackQueryHandler(lobby.host_cancel_callback, pattern=r"^host:cancel$"))
    application.add_handler(CallbackQueryHandler(lobby.lobby_join_callback, pattern=r"^lobby:join:"))
    application.add_handler(CallbackQueryHandler(lobby.lobby_leave_callback, pattern=r"^lobby:leave$"))
    application.add_handler(CallbackQueryHandler(lobby.lobby_force_start_callback, pattern=r"^lobby:forcestart$"))
    application.add_handler(CallbackQueryHandler(lobby.create_team_button_callback, pattern=r"^lobby:createteam$"))
    application.add_handler(CallbackQueryHandler(captains.captain_select_callback, pattern=r"^captain:"))
    application.add_handler(CommandHandler("changecap", captains.changecap_command))
    application.add_handler(CallbackQueryHandler(toss.toss_call_callback, pattern=r"^toss:call:"))
    application.add_handler(CallbackQueryHandler(toss.toss_decision_callback, pattern=r"^toss:decide:"))
    application.add_handler(CallbackQueryHandler(overs.overs_select_callback, pattern=r"^overs:"))
    # /batting and /bowling are now number-list text commands (see
    # handlers/batting.py and handlers/bowling.py) -- no callbacks needed.
    application.add_handler(CallbackQueryHandler(_noop_callback, pattern=r"^noop$"))

    # ---------------------------------------------------------------- #
    # Plain numeric messages: bowler DM input (1-6), batter group input (0-6),
    # and all solo-mode input. Routed centrally in gameplay.numeric_input_router.
    # ---------------------------------------------------------------- #
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"^[0-6]$"), gameplay.numeric_input_router)
    )

    # ---------------------------------------------------------------- #
    # Errors
    # ---------------------------------------------------------------- #
    application.add_error_handler(commands.error_handler)

    return application


def main() -> None:
    config.configure_logging()

    if not config.BOT_TOKEN or config.BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        logger.error(
            "No bot token configured! Set CRICKET_ROYALE_BOT_TOKEN in your .env file "
            "(see the .env this project ships with), or export it in your terminal, "
            "before running the bot."
        )
        return

    application = build_application()
    logger.info("Starting Cricket Royale bot...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
