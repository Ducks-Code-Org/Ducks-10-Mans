
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

def get_total_rounds_played_from_matchlist(matchlist) -> dict[str, int]:
    # Track all player losses
    player_total_rounds_played = {}
    for match in matchlist:
        # Now, go through each player and set their total rounds played
        # based on which team they are on
        for player in match["players"]:
            riot_name = (player["name"] + "#" + player["tag"]).lower()

            player_total_rounds_played[riot_name] = player_total_rounds_played.get(riot_name, 0) + len(match["rounds"])

    return player_total_rounds_played



def get_wins_from_match(match):
    winning_team_id = get_winning_team_id(match)
    wins = {}

    for player in match["players"]:
        player_name = (player["name"].lower() + "#" + player["tag"].lower())
        wins[player_name] = 1 if player["team_id"] == winning_team_id else 0

    return wins

def get_wins_from_matchlist(matchlist):

    wins = {}
    for match in matchlist:
        winning_team_id = get_winning_team_id(match)
        for player in match["players"]:
            player_name = (player["name"].lower() + "#" + player["tag"].lower())
            wins[player_name] = wins.get(player_name, 0) + 1 if player["team_id"] == winning_team_id else 0

    return wins

def get_losses_from_match(match):
    winning_team_id = get_winning_team_id(match)
    losses = {}

    for player in match["players"]:
        player_name = (player["name"].lower() + "#" + player["tag"].lower())
        losses[player_name] = 1 if player["team_id"] != winning_team_id else 0

    return losses


def get_losses_from_matchlist(matchlist):
    losses = {}
    for match in matchlist:
        winning_team_id = get_winning_team_id(match)

        for player in match["players"]:
            player_name = (player["name"].lower() + "#" + player["tag"].lower())
            losses[player_name] = losses.get(player_name, 0) + 1 if player["team_id"] != winning_team_id else 0

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


def get_combat_score_from_matchlist(matchlist) -> dict[str, int]:
    # Track all player combat score
    player_combat_score = {}

    for match in matchlist:
        # Find the combat score of each player
        for player in match["players"]:
            riot_name = (player["name"] + "#" + player["tag"]).lower()

            player_combat_score[riot_name] = player_combat_score.get(riot_name, 0) + player["stats"]["score"]

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

def get_deaths_from_matchlist(matchlist) -> dict[str, int]:
    # Track all player deaths
    player_deaths = {}

    for match in matchlist:
        # Find the deaths of each player
        for player in match["players"]:
            riot_name = (player["name"] + "#" + player["tag"]).lower()

            player_deaths[riot_name] = player_deaths.get(riot_name, 0) + player["stats"]["deaths"]

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

def get_kills_from_matchlist(matchlist) -> dict[str, int]:
    # Track all player kills
    player_kills = {}
    for match in matchlist:
        # Find the deaths of each player
        for player in match["players"]:
            riot_name = (player["name"] + "#" + player["tag"]).lower()

            player_kills[riot_name] = player_kills.get(riot_name, 0) + player["stats"]["kills"]

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
