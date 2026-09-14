"Lookup and display MMR and stats for a player."

from discord.ext import commands
from commands import BotCommands
from database import users
from ranks import tier_for_player
from stats_helper import DEFAULT_MMR, avg_rating_of
from tracker_links import tracker_link


async def setup(bot):
    await bot.add_cog(StatsCommand(bot))


class StatsCommand(BotCommands):
    @commands.command()
    async def stats(self, ctx, *, riot_input=None):
        # Allows players to lookup the stats of other players
        if riot_input is not None:
            try:
                riot_name, riot_tag = riot_input.rsplit("#", 1)
            except ValueError:
                await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
                return
            player_data = users.find_one(
                {"name": str(riot_name).lower(), "tag": str(riot_tag).lower()}
            )
            if player_data:
                player_id = str(player_data.get("discord_id"))
            else:
                await ctx.send(
                    "Could not find this player. Please check the name and tag and ensure they have played at least one match."
                )
                return
        else:
            player_id = str(ctx.author.id)

        if player_id in self.bot.player_mmr:
            stats_data = self.bot.player_mmr[player_id]
            mmr_value = stats_data.get("mmr", DEFAULT_MMR)
            wins = stats_data.get("wins", 0)
            losses = stats_data.get("losses", 0)
            matches_played = stats_data.get("matches_played", wins + losses)
            total_rounds_played = stats_data.get("total_rounds_played", 0)
            avg_cs = stats_data.get("average_combat_score", 0)
            kd_ratio = stats_data.get("kill_death_ratio", 0)
            # Round-weighted average VLR rating; None when never recorded.
            avg_rating = avg_rating_of(stats_data)
            avg_rating_display = (
                f"{avg_rating:.2f}" if isinstance(avg_rating, (int, float)) else "N/A"
            )
            win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0

            # Get riot name and tag
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                player_name = f"{riot_name}#{riot_tag}"
            else:
                player_name = ctx.author.name
                riot_name, riot_tag = ctx.author.name, ""

            total_players = len(self.bot.player_mmr)
            sorted_mmr = sorted(
                [
                    (pid, stats)
                    for pid, stats in self.bot.player_mmr.items()
                    if "mmr" in stats
                ],
                key=lambda x: x[1]["mmr"],
                reverse=True,
            )
            position = None
            for idx, (pid, _) in enumerate(sorted_mmr, start=1):
                if pid == player_id:
                    position = idx
                    break

            # Rank 1 tag
            if position == 1:
                rank_line = "*Supersonic Radiant!* (Rank 1)"
            else:
                rank_line = f"Rank: {position}/{total_players}"

            # MMR tier (Supersonic Radiant when holding rank 1). Players who
            # have not played this season have no tier, matching their lack
            # of a rank role (issue #159).
            tier = tier_for_player(
                mmr_value, position=position, matches_played=matches_played
            )
            tier_line = f"Tier: {tier}" if tier else "Tier: none (play a match!)"

            await ctx.send(
                f"**{tracker_link(riot_name, riot_tag)}'s Stats:**\n"
                f"MMR: {mmr_value}\n"
                f"{tier_line}\n"
                f"{rank_line}\n"
                f"Wins: {wins}\n"
                f"Losses: {losses}\n"
                f"Win%: {win_percent:.2f}%\n"
                f"Matches Played: {matches_played}\n"
                f"Total Rounds Played: {total_rounds_played}\n"
                f"Avg Rating: {avg_rating_display}\n"
                f"Average Combat Score: {avg_cs:.2f}\n"
                f"Kill/Death Ratio: {kd_ratio:.2f}"
            )
        else:
            await ctx.send(
                "You do not have an MMR yet. Participate in matches to earn one!"
            )
