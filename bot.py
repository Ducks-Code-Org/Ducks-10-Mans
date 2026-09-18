"""Hold various general functions of the bot."""

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from commands.leaderboard import LeaderboardCommand
from database import mmr_collection, seasons, users
from game.stats_helper import DEFAULT_MMR
from views.signup_view import SignupView

log = logging.getLogger(__name__)


class CustomBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 10 mans attributes
        self.signup_view: SignupView | None = None
        self.match_not_reported = False
        self.player_mmr: dict[str, dict] = {}
        self.player_names: dict[str, str] = {}
        self.match_ongoing = False
        self.selected_map: str | None = None
        self.team1: list[dict] = []
        self.team2: list[dict] = []
        self.signup_active = False
        self.current_signup_message = None
        self.current_teams_message = None
        self.queue: list[dict] = []
        self.captain1: dict | None = None
        self.captain2: dict | None = None
        self.chosen_mode: str | None = None

        self.match_channel = None
        self.match_role = None
        self.match_name = "10-Mans"

        # Increments every time a match-setup cycle starts or is cancelled.
        # Setup views capture the current value and treat any change as
        # "this setup was cancelled or superseded" (e.g. by !cancel).
        self.setup_generation = 0

        # Discord log mirror flush task (started in on_ready).
        self.mirror_flush_loop = None

        # Duck Coins (issue #34)
        self.bet_session: dict | None = None
        self.double_downs: set[str] = set()
        self.map_override_last: int = 0
        self.map_override_last_by: str | None = None
        self.map_override_deadline: float | None = None

        self.load_mmr_data()
        seasons.update_one(
            {"_id": "current"},
            {
                "$setOnInsert": {
                    "_id": "current",
                    "matches_played": 0,
                    "season_number": 0,
                    "winner_mmr": None,
                    "winner_name": None,
                    "winner_player_id": None,
                    "started_at": datetime.now(timezone.utc),
                    "ended_at": None,
                }
            },
            upsert=True,
        )

    def create_new_season(self, *, reset_player_stats: bool = True, winner) -> dict:
        current = seasons.find_one({"_id": "current"})
        current_num = int(current.get("season_number", 0))
        next_num = current_num + 1

        # Save Old Season
        old_season_obj = {
            "_id": current_num,
            "matches_played": current.get("matches_played"),
            "season_number": current_num,
            "winner_mmr": winner.get("mmr"),
            "winner_name": winner.get("name"),
            "winner_player_id": winner.get("player_id"),
            "started_at": current.get("started_at"),
            "ended_at": datetime.now(timezone.utc),
        }
        seasons.insert_one(old_season_obj)

        # Create New Season

        new_season_obj = {
            "matches_played": 0,
            "season_number": next_num,
            "winner_mmr": None,
            "winner_name": None,
            "winner_player_id": None,
            "started_at": datetime.now(timezone.utc),
            "ended_at": None,
        }

        seasons.update_one(
            {"_id": "current"},
            {"$set": new_season_obj},
            upsert=True,
        )

        # Duck Coins are per-season currency: they reset with the stats
        # reset, so `!newseason noreset` preserves coin balances too.
        # Per-match coin state (bet escrow, doubledowns, map-override
        # escalation) is per-match, not per-season, so it is always dropped.
        from game.duck_coins import clear_season_coin_state, reset_all_coins

        clear_season_coin_state(self)
        if reset_player_stats:
            reset_all_coins()
            self._reset_all_players_for_new_season(next_num)

        return new_season_obj

    def _reset_all_players_for_new_season(self, season_number: int) -> None:
        """
        Hard reset of everyone’s per‑season stats and MMR in the correct collections.
        Also resets in-memory caches so commands reflect the reset immediately.
        """
        # Reset core 10-mans stats in db. MMR starts at 0 and is re-seeded
        # (100×VLR) after each player's first reported match of the season.
        # (Duck Coins are zeroed by create_new_season alongside this reset.)
        mmr_collection.update_many(
            {},
            {
                "$set": {
                    "mmr": DEFAULT_MMR,
                    "wins": 0,
                    "losses": 0,
                    "total_combat_score": 0,
                    "total_kills": 0,
                    "total_deaths": 0,
                    "matches_played": 0,
                    "total_rounds_played": 0,
                    "average_combat_score": 0,
                    "kill_death_ratio": 0,
                    "total_rating_points": 0.0,
                    "total_rating_rounds": 0,
                    "avg_rating": None,
                    "previous_rank": None,
                    "current_rank": None,
                }
            },
        )

        # 3) Reset in-memory cache
        for _pid, stats in list(self.player_mmr.items()):
            stats.update(
                {
                    "mmr": DEFAULT_MMR,
                    "wins": 0,
                    "losses": 0,
                    "total_combat_score": 0,
                    "total_kills": 0,
                    "total_deaths": 0,
                    "matches_played": 0,
                    "total_rounds_played": 0,
                    "average_combat_score": 0,
                    "kill_death_ratio": 0,
                    "total_rating_points": 0.0,
                    "total_rating_rounds": 0,
                }
            )

        self.load_mmr_data()

    def load_mmr_data(self):
        self.player_mmr.clear()
        self.player_names.clear()

        # mmr_data is keyed by player_id (the Discord id). If duplicate docs
        # exist for a player, prefer the one with real stats so a stale
        # default doc can't silently zero out a player's record.
        doc_scores = {}
        for doc in mmr_collection.find():
            player_id = doc["player_id"]
            entry = {
                "mmr": doc.get("mmr", DEFAULT_MMR),
                "wins": doc.get("wins", 0),
                "losses": doc.get("losses", 0),
                "total_combat_score": doc.get("total_combat_score", 0),
                "total_kills": doc.get("total_kills", 0),
                "total_deaths": doc.get("total_deaths", 0),
                "matches_played": doc.get("matches_played", 0),
                "total_rounds_played": doc.get("total_rounds_played", 0),
                "average_combat_score": doc.get("average_combat_score", 0),
                "kill_death_ratio": doc.get("kill_death_ratio", 0),
                "total_rating_points": doc.get("total_rating_points", 0.0),
                "total_rating_rounds": doc.get("total_rating_rounds", 0),
            }
            score = (
                doc.get("matches_played", 0),
                doc.get("wins", 0) + doc.get("losses", 0),
            )

            if player_id not in self.player_mmr or score > doc_scores[player_id]:
                self.player_mmr[player_id] = entry
                doc_scores[player_id] = score

    def save_mmr_data(self):
        for player_id, stats in self.player_mmr.items():
            # Get the Riot name and tag from the users collection
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = "Unknown"
            mmr_collection.update_one(
                {"player_id": player_id},
                {
                    "$set": {
                        "mmr": stats["mmr"],
                        "wins": stats["wins"],
                        "losses": stats["losses"],
                        "name": name,
                        "total_combat_score": stats.get("total_combat_score", 0),
                        "total_kills": stats.get("total_kills", 0),
                        "total_deaths": stats.get("total_deaths", 0),
                        "matches_played": stats.get("matches_played", 0),
                        "total_rounds_played": stats.get("total_rounds_played", 0),
                        "average_combat_score": stats.get("average_combat_score", 0),
                        "kill_death_ratio": stats.get("kill_death_ratio", 0),
                        "total_rating_points": stats.get("total_rating_points", 0.0),
                        "total_rating_rounds": stats.get("total_rating_rounds", 0),
                    }
                },
                upsert=True,
            )

    def ensure_player_mmr(self, player_id, player_names):
        if player_id not in self.player_mmr:
            self._init_player_mmr_entry(player_id)
        elif self.player_mmr[player_id].get("matches_played", -1) == -1:
            # Entry exists but was never populated with defaults (e.g. from
            # load_mmr_data() where the player had no db doc).  Fill in defaults.
            self._init_player_mmr_entry(player_id)

        user_data = users.find_one({"discord_id": str(player_id)})
        if user_data:
            player_names[player_id] = user_data.get("name", "Unknown")
        else:
            player_names[player_id] = "Unknown"

    def _init_player_mmr_entry(self, player_id):
        self.player_mmr[player_id] = {
            "mmr": DEFAULT_MMR,
            "wins": 0,
            "losses": 0,
            "total_combat_score": 0,
            "total_kills": 0,
            "total_deaths": 0,
            "matches_played": 0,
            "total_rounds_played": 0,
            "average_combat_score": 0,
            "kill_death_ratio": 0,
            "total_rating_points": 0.0,
            "total_rating_rounds": 0,
        }

    async def setup_hook(self):
        await self.load_extension("commands.admin_commands")
        await self.load_extension("commands.bug")
        await self.load_extension("commands.coin_commands")
        await self.load_extension("commands.help")
        await self.load_extension("commands.interest")
        await self.load_extension("commands.leaderboard")
        await self.load_extension("commands.linkriot")
        await self.load_extension("commands.maintenance_commands")
        await self.load_extension("commands.ranks")
        await self.load_extension("commands.report")
        await self.load_extension("commands.signup")
        await self.load_extension("commands.stats")
        self.tree.on_error = self._on_app_command_error
        await self.tree.sync()
        log.info("Bot is ready and cogs are loaded.")

    async def on_ready(self):
        log.info("Bot connected as %s.", self.user)

        # Crash safety: refund any bets/doubledowns the previous run left
        # escrowed (e.g. the process died mid betting window). Journal is
        # written after every mutation and cleared on settle/refund, so a
        # surviving journal means settlement never happened.
        from game.duck_coins import recover_orphaned_escrow

        recover_orphaned_escrow(self)

        # Start flushing WARNING+ records into #bot-logs now that guilds
        # are cached (the handler is created in main.py before run()).
        handler = getattr(self, "discord_log_handler", None)
        if handler is not None:
            # Attach the bot so the handler can resolve #bot-logs; without
            # this every queued record is silently dropped.
            handler.bot = self
            # on_ready can fire again after a reconnect; keep one flush task.
            if self.mirror_flush_loop is None or self.mirror_flush_loop.done():
                self.mirror_flush_loop = self.loop.create_task(
                    self._flush_discord_logs(handler)
                )

        await self.purge_old_match_roles()
        await self.purge_old_match_channels()
        await self.send_new_leaderboard()

    async def _flush_discord_logs(self, handler):
        """Periodically drain queued log records into #bot-logs."""
        import asyncio

        try:
            while True:
                await asyncio.sleep(5)
                await handler.flush_pending()
        except asyncio.CancelledError:
            pass

    async def on_command(self, ctx):
        log.info(
            "Command !%s invoked by %s in #%s", ctx.command, ctx.author, ctx.channel
        )

    async def on_app_command_completion(self, interaction, command):
        log.info(
            "Slash command /%s invoked by %s in #%s",
            getattr(command, "qualified_name", command),
            interaction.user,
            interaction.channel,
        )

    async def _on_app_command_error(self, interaction, error):
        log.error(
            "Error in slash command /%s by %s: %r",
            getattr(interaction.command, "qualified_name", interaction.command),
            interaction.user,
            error,
            exc_info=error,
        )

    async def on_error(self, event_method, /, *args, **kwargs):
        # Last-resort handler for any event not covered by a specific
        # try/except (e.g. on_ready startup tasks).
        log.error("Unhandled exception in event %s", event_method, exc_info=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            log.debug("Unknown command from %s: %s", ctx.author, ctx.message.content)
            return
        if isinstance(error, commands.MissingPermissions):
            log.warning("%s lacks permissions for !%s", ctx.author, ctx.command)
        elif isinstance(error, (commands.MissingRole, commands.MissingAnyRole)):
            log.warning("%s lacks role for !%s", ctx.author, ctx.command)
        elif isinstance(error, commands.CheckFailure):
            log.warning("Check failed for !%s by %s", ctx.command, ctx.author)
        else:
            log.error(
                "Unhandled error in !%s by %s: %r",
                ctx.command,
                ctx.author,
                error,
                exc_info=error,
            )

    async def purge_old_match_roles(self):
        log.info("Checking for old match roles to delete...")
        found_any = False
        for guild in self.guilds:
            # Find roles with 'match' in the name (case-insensitive)
            old_roles = [r for r in guild.roles if "match" in r.name.lower()]
            if not old_roles:
                continue
            found_any = True
            log.info(
                "Deleting roles in guild '%s': %s",
                guild.name,
                [role.name for role in old_roles],
            )
            for role in old_roles:
                try:
                    await role.delete()
                except discord.HTTPException:
                    pass
        if not found_any:
            log.info("No old roles found.")

    async def purge_old_match_channels(self):
        log.info("Checking for old match channels to delete...")
        found_any = False
        for guild in self.guilds:
            # Find channels with 'match' in the name (case-insensitive)
            old_channels = [c for c in guild.channels if "match" in c.name.lower()]
            if not old_channels:
                continue
            found_any = True
            log.info(
                "Deleting channels in guild '%s': %s",
                guild.name,
                [channel.name for channel in old_channels],
            )
            for channel in old_channels:
                try:
                    await channel.delete()
                except discord.HTTPException:
                    pass
        if not found_any:
            log.info("No old channels found.")

    async def send_new_leaderboard(self):
        # If there is a channel named #leaderboard in any guild, send a new leaderboard
        # Leaderboard matches the response from the `!leaderboard` command
        log.info("Sending new leaderboard to all #leaderboard channels...")

        for guild in self.guilds:
            leaderboard_channel = discord.utils.get(
                guild.text_channels, name="leaderboard"
            )
            if leaderboard_channel:
                try:
                    # Delete old messages containing 'leaderboard' in the channel
                    async for message in leaderboard_channel.history(limit=100):
                        if (
                            message.author == self.user
                            and "leaderboard" in message.content.lower()
                        ) or "!leaderboard" in message.content.lower():
                            try:
                                await message.delete()
                            except Exception:
                                pass

                    leaderboard_view, content, error = (
                        LeaderboardCommand.generate_leaderboard(self, None, "mmr")
                    )
                    if error:
                        await leaderboard_channel.send(content=error)
                    else:
                        await leaderboard_channel.send(
                            content=content, view=leaderboard_view, silent=True
                        )
                except Exception as e:
                    log.error("Failed to send leaderboard: %s", e)
