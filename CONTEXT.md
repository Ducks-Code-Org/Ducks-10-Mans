# Ducks-10-Mans

A Discord bot that runs signup queues for 10-man pick-up matches, drafts teams, tracks results and ranks, and moves players through voice channels.

## Language

### Signup flow

**Signup queue**:
The ordered list of players who have signed up for the next match, capped at ten.
_Avoid_: signup list, queue roster

**Lobby wait**:
The 10-minute window after the signup queue fills, during which all queued players must join the lobby voice channel before match setup begins.
_Avoid_: voice check, presence check, join phase

**Missing players**:
Queued players who are not currently in the lobby voice channel during the lobby wait.
_Avoid_: absent users, no-shows

**Final warning**:
The message sent two minutes before the lobby wait expires; it pings the missing players and carries the live countdown.
_Avoid_: timeout ping, 2-minute ping

**Live countdown**:
The minute-and-seconds remaining display, edited every second onto the final warning message until the lobby wait ends or the match is cancelled.
_Avoid_: timer message, clock

### Player changes

**Substitute**:
An admin-initiated swap of a queued or rostered player for a replacement player, allowed from the start of the lobby wait onwards.
_Avoid_: sub-out, replacement (verb)

**Sub announcement**:
The channel message naming the incoming and outgoing players of a substitute.

### Rating

**MMR**:
The per-player rating a match report moves; it exists only as a whole number at or above zero and drives rank tiers and the leaderboard.
_Avoid_: elo (except for the legacy offline tool)

**Minimum gain**:
The smallest amount of MMR any win can pay.
_Avoid_: win floor, gain boost

**Minimum loss**:
The smallest amount of MMR any loss can cost.
_Avoid_: loss floor, loss cap
