"""This file is not connected to the bot. The purpose is to be able to add match data to the database in case something goes wrong"""

from DebugTools.helpers.match_helper_functions import display_match_info, get_custom_matchlist
from DebugTools.helpers.change_helper_functions import confirm_changes, get_changes_that_will_be_made


def get_match_to_upload(matchlist):
    while True:
        print(f"Select match to upload (0-{len(matchlist) - 1})")

        for i in range(len(matchlist)):
            print(f"{i}:")
            display_match_info(matchlist[i])
            print("")

        match_index = int(input("Enter match number: "))

        print(f"\nYou selected match {match_index}:")
        display_match_info(matchlist[match_index])

        confirm_match = input("Are you sure you want to upload this match (Y/n)? ").lower()
        if confirm_match == "y":
            confirm_changes(get_changes_that_will_be_made(matchlist[match_index]), matchlist[match_index])
            return matchlist[match_index]
        print("")


if __name__ == "__main__":
    recent_matches = get_custom_matchlist("duck", "mst")

    get_match_to_upload(recent_matches)
