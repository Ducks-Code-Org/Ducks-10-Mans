from stat_change import StatChange

def display_change(change: StatChange):
    print(f"({change.player_name}) {change.collection.name}-{change.stat_name}: {change.old}->{change.new}")

def display_all_changes(stat_changes: list[StatChange]):
    for change in stat_changes:
        display_change(change)