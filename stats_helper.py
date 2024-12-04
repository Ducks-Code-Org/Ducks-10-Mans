from database import users, mmr_collection
from globals import player_mmr, player_names
# Update stats
def update_stats(player_stats, total_rounds):
    name = player_stats.get("name")
    tag = player_stats.get("tag")

    user_entry = users.find_one({"name": name, "tag": tag})
    if not user_entry:
        print(f"Player {name}#{tag} not linked to any Discord account.")
        return

    discord_id = int(user_entry.get("discord_id"))

    # Get the stats
    stats = player_stats.get("stats", {})
    score = stats.get("score", 0)
    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)

    if discord_id in player_mmr:
        player_data = player_mmr[discord_id]
        total_matches = player_data.get("matches_played", 0) + 1
        total_combat_score = player_data.get("total_combat_score", 0) + score
        total_kills = player_data.get("total_kills", 0) + kills
        total_deaths = player_data.get("total_deaths", 0) + deaths
        total_rounds_played = player_data.get("total_rounds_played", 0) + total_rounds

        average_combat_score = total_combat_score / total_rounds_played if total_rounds_played > 0 else 0
        kill_death_ratio = total_kills / total_deaths if total_deaths > 0 else total_kills

        player_mmr[discord_id].update({
            "total_combat_score": total_combat_score,
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "matches_played": total_matches,
            "total_rounds_played": total_rounds_played,
            "average_combat_score": average_combat_score,
            "kill_death_ratio": kill_death_ratio
        })

    else:
        total_matches = 1
        total_combat_score = score
        total_kills = kills
        total_deaths = deaths
        total_rounds_played = total_rounds
        average_combat_score = total_combat_score / total_rounds_played if total_rounds_played > 0 else 0
        kill_death_ratio = total_kills / total_deaths if total_deaths > 0 else total_kills

        player_mmr[discord_id] = {
            'mmr': 1000,
            'wins': 0,
            'losses': 0,
            'total_combat_score': total_combat_score,
            'total_kills': total_kills,
            'total_deaths': total_deaths,
            'matches_played': total_matches,
            'total_rounds_played': total_rounds_played,
            'average_combat_score': average_combat_score,
            'kill_death_ratio': kill_death_ratio
        }
        player_names[discord_id] = name

        mmr_collection.update_one(
            {'player_id': discord_id},
            {'$set': {
                'mmr': 1000,
                'wins': 0,
                'losses': 0,
                'name': name,
                'total_combat_score': total_combat_score,
                'total_kills': total_kills,
                'total_deaths': total_deaths,
                'matches_played': total_matches,
                'total_rounds_played': total_rounds_played,
                'average_combat_score': average_combat_score,
                'kill_death_ratio': kill_death_ratio
            }},
            upsert=True
        )