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

### Interest boards

**Interest board**:
The message that announces an interest slot and carries its Join / Remove / Refresh buttons.
_Avoid_: signup board, polling message

**Interest slot**:
A planned time to run Duck’s 10 Mans, announced on an interest board and identified by its exact time; retired once that time passes.
_Avoid_: signup slot, interest check

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

**Leaderboard rank**:
A player's 1-based position in the canonical leaderboard order (MMR descending, then matches played, wins, and player_id); players who have never played have none.
_Avoid_: standing, placement

**Rank tier**:
The MMR-bucket role a player holds, unrelated to leaderboard position.
_Avoid_: leaderboard rank, placement

### Betting

**Parimutuel pool**:
The coins escrowed on both betting sides for a match; each winning bettor's share is their stake's proportion of the whole pool, rounded half-up.
_Avoid_: pot, prize pool

**Payout floor**:
The guarantee that every winning bet pays at least 1.5x its stake, with the bot minting any top-up the pool cannot fund.
_Avoid_: minimum payout, win guarantee

**Unclaimed pool**:
The pool that sinks with no payout when nobody bet on the winning side.
_Avoid_: dead pool, lost pool

### Match setup votes

**Vote board**:
The message that announces a setup vote and carries its vote buttons and live tallies.
_Avoid_: poll message, vote embed

**Vote confirmation**:
The ephemeral reply shown to a voter after their click, naming the choice they voted for.
_Avoid_: vote ack, vote receipt

**Reflect-before-confirm**:
The invariant that a vote confirmation must never become visible before the vote board shows the vote it confirms; the board write rides the voter's own interaction and the confirmation follows it.
_Avoid_: ack-first, optimistic confirm
