"""This file provides functions for updating players stats."""

from database import users, mmr_collection

# Update stats
def update_stats(player_stats, total_rounds, player_mmr, player_names):
    """Update player stats with proper initialization and error handling"""
    name = player_stats.get("name", "").lower()
    tag = player_stats.get("tag", "").lower()

    user_entry = users.find_one({"name": name, "tag": tag})
    if not user_entry:
        print(f"Player {name}#{tag} not linked to any Discord account.")
        return

    discord_id = str(user_entry.get("discord_id"))

    # Get the stats with proper defaults
    stats = player_stats.get("stats", {})
    score = stats.get("score", 0)
    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)

    if discord_id in player_mmr:
        player_data = player_mmr[discord_id]
        # Initialize missing fields with defaults
        player_data.setdefault("matches_played", 0)
        player_data.setdefault("total_combat_score", 0)
        player_data.setdefault("total_kills", 0)
        player_data.setdefault("total_deaths", 0)
        player_data.setdefault("total_rounds_played", 0)
        
        # Update stats
        total_matches = player_data["matches_played"] + 1
        total_combat_score = player_data["total_combat_score"] + score
        total_kills = player_data["total_kills"] + kills
        total_deaths = player_data["total_deaths"] + deaths
        total_rounds_played = player_data["total_rounds_played"] + total_rounds

        # Calculate averages
        average_combat_score = total_combat_score / total_rounds_played if total_rounds_played > 0 else 0
        kill_death_ratio = total_kills / total_deaths if total_deaths > 0 else total_kills

        # Update player_mmr dictionary
        player_mmr[discord_id].update({
            "total_combat_score": total_combat_score,
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "matches_played": total_matches,
            "total_rounds_played": total_rounds_played,
            "average_combat_score": average_combat_score,
            "kill_death_ratio": kill_death_ratio
        })

        # Update database with all fields
        mmr_collection.update_one(
            {"player_id": discord_id},
            {
                "$set": {
                    "mmr": player_mmr[discord_id].get("mmr", 1000),
                    "wins": player_mmr[discord_id].get("wins", 0),
                    "losses": player_mmr[discord_id].get("losses", 0),
                    "total_combat_score": total_combat_score,
                    "total_rounds_played": total_rounds_played,
                    "matches_played": total_matches,
                    "average_combat_score": average_combat_score,
                    "kill_death_ratio": kill_death_ratio,
                    "total_kills": total_kills,
                    "total_deaths": total_deaths
                }
            },
            upsert=True
        )

    else:
        # Initialize new player stats
        total_matches = 1
        total_combat_score = score
        total_kills = kills
        total_deaths = deaths
        total_rounds_played = total_rounds
        average_combat_score = total_combat_score / total_rounds_played if total_rounds_played > 0 else 0
        kill_death_ratio = total_kills / total_deaths if total_deaths > 0 else total_kills

        player_mmr[discord_id] = {
            "mmr": 1000,
            "wins": 0,
            "losses": 0,
            "total_combat_score": total_combat_score,
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "matches_played": total_matches,
            "total_rounds_played": total_rounds_played,
            "average_combat_score": average_combat_score,
            "kill_death_ratio": kill_death_ratio
        }
        player_names[discord_id] = name

        # Insert new player into database with all fields
        mmr_collection.update_one(
            {"player_id": discord_id},
            {
                "$set": {
                    "mmr": 1000,
                    "wins": 0,
                    "losses": 0,
                    "name": name,
                    "total_combat_score": total_combat_score,
                    "total_kills": total_kills,
                    "total_deaths": total_deaths,
                    "matches_played": total_matches,
                    "total_rounds_played": total_rounds_played,
                    "average_combat_score": average_combat_score,
                    "kill_death_ratio": kill_death_ratio
                }
            },
            upsert=True
        )