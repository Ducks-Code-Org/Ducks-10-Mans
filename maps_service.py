"Web scraper to get the list of current maps for each gamemode from the Valorant wiki"

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4"])
    from bs4 import BeautifulSoup

URL = "https://valorant.fandom.com/wiki/Maps"


def get_standard_maps() -> list[str]:
    print("Fetching all standard maps...")
    response = requests.get(URL, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    # Find the table with the "Standard" header
    standard_header = soup.find("h3", id="Standard")
    if not standard_header:
        # Fallback: find by text
        for h3 in soup.find_all("h3"):
            if "Standard" in h3.get_text():
                standard_header = h3
                break
    if not standard_header:
        print("Standard maps table not found.")
        return []
    # The table is after the header
    table = standard_header.find_next("table")
    maps = []
    for row in table.find_all("tr")[1:]:  # skip header row
        first_td = row.find("td")
        if first_td:
            # Find all <a> tags in the first <td>
            a_tags = first_td.find_all("a", title=True)
            if a_tags:
                # The last <a> is the one with the map name
                map_name = a_tags[-1].get_text(strip=True)
                if map_name:
                    maps.append(map_name)
    print("Standard maps found:")
    print(maps)
    return maps


def get_competitive_maps() -> list[str]:
    print("Fetching standard competitive maps...")
    response = requests.get(URL, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    # Look for any <th> containing 'Current rotation'
    for th in soup.find_all("th"):
        if (
            th.get_text(strip=True).lower().startswith("current rotation")
            or "current rotation" in th.get_text(strip=True).lower()
        ):
            table = th.find_parent("table")
            break

    if table:
        divs = table.find_all("div", class_="gallery-image-wrapper")
        ids = [div.get("id") for div in divs if div.get("id")]
        print("Competitive maps found:")
        print(ids)
        return ids
    else:
        print("Current rotation table not found.")
        return []


def get_tdm_maps() -> list[str]:
    print("Fetching all Team Deathmatch maps...")
    response = requests.get(URL, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    # Find the "Team Deathmatch" header
    tdm_header = soup.find("h3", id="Team Deathmatch")
    if not tdm_header:
        # Fallback: find by text
        for h3 in soup.find_all("h3"):
            if "Team Deathmatch" in h3.get_text():
                tdm_header = h3
                break
    if not tdm_header:
        print("Team Deathmatch maps table not found.")
        return []
    # The table is after the header
    table = tdm_header.find_next("table")
    maps = []
    for row in table.find_all("tr")[1:]:  # skip header row
        first_td = row.find("td")
        if first_td:
            # The map name is the text after the <br> tag
            br = first_td.find("br")
            if br and br.next_sibling:
                map_name = br.next_sibling.strip()
                if map_name:
                    maps.append(map_name)
    print("Team Deathmatch maps found:")
    print(maps)
    return maps
