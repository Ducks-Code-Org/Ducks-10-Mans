from stat_change import StatChange, FieldNotFound

# Get total rounds played for each player in the match
def get_total_rounds_played_from_match(match) -> dict[str, int]:
    # Track all player losses
    player_total_rounds_played = {}

    # Now, go through each player and set their total rounds played
    # based on which team they are on
    for player in match["players"]:
        riot_name = (player["name"] + "#" + player["tag"]).lower()

        player_total_rounds_played[riot_name] = len(match["rounds"])

    return player_total_rounds_played

def get_wins_from_match(match):
    """
    Returns a dictionary of player names and the number of wins they earned based on the match outcome.

    Args:
        match (dict): The match data.

    Returns:
        dict: A dictionary mapping player names to wins (1 if their team won, 0 otherwise).
    """
    winning_team_id = get_winning_team_id(match)
    wins = {}

    for player in match["players"]:
        player_name = (player["name"].lower() + "#" + player["tag"].lower())
        wins[player_name] = 1 if player["team_id"] == winning_team_id else 0

    return wins

def get_losses_from_match(match):
    """
    Returns a dictionary of player names and the number of losses they earned based on the match outcome.

    Args:
        match (dict): The match data.

    Returns:
        dict: A dictionary mapping player names to losses (1 if their team lost, 0 otherwise).
    """
    winning_team_id = get_winning_team_id(match)
    losses = {}

    for player in match["players"]:
        player_name = (player["name"].lower() + "#" + player["tag"].lower())
        losses[player_name] = 1 if player["team_id"] != winning_team_id else 0

    return losses

# Get combat score for each player in the match
def get_combat_score_from_match(match) -> dict[str, int]:
    # Track all player combat score
    player_combat_score = {}

    # Find the combat score of each player
    for player in match["players"]:
        riot_name = (player["name"] + "#" + player["tag"]).lower()

        player_combat_score[riot_name] = player["stats"]["score"]

    return player_combat_score


# Get deaths for each player in the match
def get_deaths_from_match(match) -> dict[str, int]:
    # Track all player deaths
    player_deaths = {}

    # Find the deaths of each player
    for player in match["players"]:
        riot_name = (player["name"] + "#" + player["tag"]).lower()

        player_deaths[riot_name] = player["stats"]["deaths"]

    return player_deaths


# Get kills for each player in the match
def get_kills_from_match(match) -> dict[str, int]:
    # Track all player kills
    player_kills = {}

    # Find the deaths of each player
    for player in match["players"]:
        riot_name = (player["name"] + "#" + player["tag"]).lower()

        player_kills[riot_name] = player["stats"]["kills"]

    return player_kills

def get_winning_team_id(match):
    teams = match["teams"]
    blue_score = 0
    red_score = 0
    for team in teams:
        if team["team_id"] == "Blue":
            blue_score += team["rounds"]["won"]
        elif team["team_id"] == "Red":
            red_score += team["rounds"]["won"]

    if blue_score > red_score:
        return "Blue"
    elif red_score > blue_score:
        return "Red"
    else:
        return "Draw"