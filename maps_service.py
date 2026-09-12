"Web scraper to get the list of current maps for each gamemode from the Valorant wiki."

import requests
from bs4 import BeautifulSoup

# Fallback lists are used when the site can't be reached or parsing yields
# nothing, so match setup always has a map pool available.
FALLBACK_STANDARD_MAPS = [
    "Abyss",
    "Ascent",
    "Bind",
    "Breeze",
    "Corrode",
    "Fracture",
    "Haven",
    "Icebox",
    "Lotus",
    "Pearl",
    "Split",
    "Sunset",
]
FALLBACK_COMPETITIVE_MAPS = [
    "Fracture",
    "Lotus",
    "Ascent",
    "Split",
    "Haven",
    "Breeze",
]

URL = "https://blitz.gg/valorant/stats/maps"


def _scrape_map_names(url: str, fallback: list[str]) -> list[str]:
    """Fetch a blitz.gg stats page and return unique map names from its table."""
    print("Fetching map list...")
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        maps: list[str] = []
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) <= 1:
                continue
            # The map name is in the second column, and is repeated
            # (e.g., 'Lotus Lotus').
            map_cell = cols[1].get_text(strip=True)
            if not map_cell:
                continue
            name_parts = map_cell.split()
            map_name = (
                name_parts[0]
                if len(name_parts) == 2 and name_parts[0] == name_parts[1]
                else map_cell
            )
            if map_name not in maps:
                maps.append(map_name)
        if not maps:
            print("Warning: No maps found. Using a potentially outdated map list.")
            return fallback
        return maps
    except requests.RequestException as e:
        print(
            f"Warning: network error or timeout ({e}). Using a potentially outdated map list."
        )
        return fallback


def get_standard_maps() -> list[str]:
    return _scrape_map_names(URL + "?queue=unrated", FALLBACK_STANDARD_MAPS)


def get_competitive_maps() -> list[str]:
    return _scrape_map_names(URL, FALLBACK_COMPETITIVE_MAPS)
