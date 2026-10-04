# Ducks-10-Mans

A Discord bot that runs signup queues for 10-man pick-up matches, drafts teams, tracks results and ranks, and moves players through voice channels.

## Language

### Signup flow

**Signup session**:
One full `/signup` cycle — from starting the queue, through filling it, to match setup, report, or cancellation. Resource cleanup and staleness checks are scoped to a session, not to the queue alone.
_Avoid_: signup (when the queue vs. the whole cycle is meant)

**Signup queue**:
The ordered list of players who have signed up for the next match, capped at ten.
_Avoid_: signup list, queue roster

**Signup timeout**:
The automatic cancellation of a signup session whose queue stayed empty for ten minutes, or inactive for two hours. Unlike an admin cancel it runs unattended, so it must leave no trace: deleted match resources are released immediately, remembered for `/pingrecent`.
_Avoid_: cleanup (an unattended cancel is not janitorial cleanup), session expire

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

**Dodge**:
A match player's paid cancellation of the live match during the powerup window: the match tears down like an admin cancel, all match coins except the dodger's fee are refunded, and #10-mans is told who dodged by server nickname. The fee is burned and never refunded.
_Avoid_: coin cancellation, paid cancel (when the attributed announcement is the point)

### Match setup

**Powerup window**:
The 2 minutes after teams are announced, plus any `/setmap` extensions; the only period in which the player powerups (`/setmap`, `/doubledown`, `/dodge`) are usable.
_Avoid_: grace window (when talking to users), doubledown window, map override window

**Vote board**:
The public match-setup message whose buttons carry the running vote counts of a setup vote (mode, map pool, map).
_Avoid_: vote message, tally post

**Vote confirmation**:
The ephemeral reply telling a player their setup vote was recorded; it must never be visible before the vote board shows the vote it confirms.
_Avoid_: vote ack, voted reply

### Planning

**Interest slot**:
A planned time for Duck's 10 Mans, announced on a board message whose buttons record who is in. Identified by its exact time; once that time has passed, the slot is over and its board is retired.
_Avoid_: interest message, slot thread

### Rating

**MMR**:
The per-player rating a match report moves; it exists only as a whole number at or above zero and drives rank tiers and the leaderboard.
_Avoid_: elo (except for the legacy offline tool)

**Leaderboard rank**:
A player's 1-based position in the leaderboard's total order; players who have never played have none.
_Avoid_: ladder position, seed

**Rank tier**:
The MMR-bucket role a played player holds (Wood, Stone, and so on); unrelated to leaderboard position.
_Avoid_: rank (unqualified when both meanings are nearby), tier role

**Minimum gain**:
The smallest amount of MMR any win can pay.
_Avoid_: win floor, gain boost

**Minimum loss**:
The smallest amount of MMR any loss can cost.
_Avoid_: loss floor, loss cap

### Betting

**Parimutuel pool**:
Betting where spectators' stakes on both sides form one pot, and winners split it in proportion to their own stake.
_Avoid_: odds book, house odds

**Payout floor**:
The smallest payout a winning bet can receive, expressed as a multiple of its stake; top-ups beyond the pool are minted.
_Avoid_: minimum payout, guaranteed win, win floor

**Unclaimed pool**:
A pool with no bets on the winning side; its coins sink — no refunds, no consolation.
_Avoid_: dead pool, forfeited pool
