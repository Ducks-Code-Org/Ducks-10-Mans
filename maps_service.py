"Web scraper to get the list of current maps for each gamemode from the Valorant wiki"

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4"])
    from bs4 import BeautifulSoup

URL = "https://blitz.gg/valorant/stats/maps"


def get_standard_maps() -> list[str]:
    potentially_outdated_standard_map_list: list = [
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

    print("Fetching all standard maps...")

    try:
        response = requests.get(URL + "?queue=unrated", timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        # Find all table rows
        rows = soup.find_all("tr")
        maps = []
        for row in rows:
            cols = row.find_all("td")
            if len(cols) > 1:
                # The map name is in the second column, and is repeated (e.g., 'Lotus Lotus')
                map_cell = cols[1].get_text(strip=True)
                # Split by space and take the first word if repeated
                if map_cell:
                    name_parts = map_cell.split()
                    if len(name_parts) == 2 and name_parts[0] == name_parts[1]:
                        map_name = name_parts[0]
                    else:
                        map_name = map_cell
                    if map_name not in maps:
                        maps.append(map_name)
        if not maps:
            print(
                "Warning: No standard maps found. Using a potentially outdated map list."
            )
            return potentially_outdated_standard_map_list
        print("Standard maps found:")
        print(maps)
        return maps
    except Exception as e:
        print(
            f"Warning: Requests network error or timeout ({e}). Using a potentially outdated map list."
        )
        return potentially_outdated_standard_map_list


def get_competitive_maps() -> list[str]:
    potentially_outdated_competitive_map_list: list = [
        "Fracture",
        "Lotus",
        "Ascent",
        "Split",
        "Haven",
        "Lotus",
        "Breeze",
    ]

    print("Fetching standard competitive maps...")

    try:
        response = requests.get(URL, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        # Find all table rows
        rows = soup.find_all("tr")
        maps = []
        for row in rows:
            cols = row.find_all("td")
            if len(cols) > 1:
                # The map name is in the second column, and is repeated (e.g., 'Lotus Lotus')
                map_cell = cols[1].get_text(strip=True)
                # Split by space and take the first word if repeated
                if map_cell:
                    name_parts = map_cell.split()
                    if len(name_parts) == 2 and name_parts[0] == name_parts[1]:
                        map_name = name_parts[0]
                    else:
                        map_name = map_cell
                    if map_name not in maps:
                        maps.append(map_name)
        if not maps:
            print(
                "Warning: No competitive maps found. Using a potentially outdated map list."
            )
            return potentially_outdated_competitive_map_list
        print("Competitive maps found:")
        print(maps)
        return maps
    except Exception as e:
        print(
            f"Warning: Requests network error or timeout ({e}). Using a potentially outdated map list."
        )
        return potentially_outdated_competitive_map_list


def get_tdm_maps() -> list[str]:
    potentially_outdated_tdm_map_list: list = [
        "District",
        "Kasbah",
        "Piazza",
        "Drift",
        "Glitch",
    ]

    print("Fetching all Team Deathmatch maps...")

    try:
        response = requests.get(URL + "?queue=hurm", timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        # Find all table rows
        rows = soup.find_all("tr")
        maps = []
        for row in rows:
            cols = row.find_all("td")
            if len(cols) > 1:
                # The map name is in the second column, and is repeated (e.g., 'Lotus Lotus')
                map_cell = cols[1].get_text(strip=True)
                # Split by space and take the first word if repeated
                if map_cell:
                    name_parts = map_cell.split()
                    if len(name_parts) == 2 and name_parts[0] == name_parts[1]:
                        map_name = name_parts[0]
                    else:
                        map_name = map_cell
                    if map_name not in maps:
                        maps.append(map_name)
        if not maps:
            print(
                "Warning: No Team Deathmatch maps found. Using a potentially outdated map list."
            )
            return potentially_outdated_tdm_map_list
        print("Team Deathmatch maps found:")
        print(maps)
        return maps
    except Exception as e:
        print(
            f"Warning: Requests network error or timeout ({e}). Using a potentially outdated map list."
        )
        return potentially_outdated_tdm_map_list
