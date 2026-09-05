import json

p = r"C:\Users\nnnev\Desktop\Git Repos and Coding\Ducks 10 Men\Ducks-10-Mans\backups\revert_backup_ceff7f19-efea-4ead-bc74-9fe4b4ca1a86_20260905_060129.json"
d = json.load(open(p))
print("mmr_docs count:", len(d["collections"]["mmr_data"]))
for doc in d["collections"]["mmr_data"]:
    name = doc.get("name", "NO_NAME")
    pid = doc.get("player_id", "?")
    mmr = doc.get("mmr", "?")
    mp = doc.get("matches_played", 0)
    tk = doc.get("total_kills", 0)
    print(f"  {name}: player_id={pid} mmr={mmr} matches={mp} kills={tk}")
