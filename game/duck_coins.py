"""Duck Coins currency: awarding, betting, doubledown, and map overrides (issue #34)."""

import asyncio
import logging
import time

import discord

from database import coin_escrow, mmr_collection, users
from game.stats_helper import DEFAULT_MMR
from globals import feature_enabled
from services.maps_service import get_standard_maps
from tracker_links import display_line_for

log = logging.getLogger(__name__)

BET_WINDOW_SECONDS = 300
BET_TICK_SECONDS = 30
DOUBLEDOWN_COST = 5
SETMAP_BASE_COST = 3
# After teams finalize (match_ongoing flips True), !setmap stays usable this
# long — a grace window for last-second map swaps in both modes.
SETMAP_GRACE_SECONDS = 120
# The match-channel powerup notice counts this window down live.
POWERUP_TICK_SECONDS = 15


def duck_coins_enabled() -> bool:
    return feature_enabled("duck_coins")


# ---------------------------------------------------------------------------
# Crash-safety journal: the bet escrow / doubledown set live only in the bot
# object, so a crash mid-window would silently void escrowed coins (no refund,
# no payout — the coins were already deducted). The journal mirrors that state
# into Mongo after every mutation; on startup, recover_orphaned_escrow()
# refunds anything still journaled from before the restart.
# ---------------------------------------------------------------------------

_ESCROW_DOC_ID = "open_bets"


def persist_escrow(bot) -> None:
    """Mirror in-memory coin state (bet session, doubledowns, map-override
    escalation chain) into Mongo.

    Best-effort: persistence failures are logged and swallowed so a transient
    DB hiccup can never break an in-progress bet, doubledown, or override.
    """
    session = getattr(bot, "bet_session", None)
    chain = getattr(bot, "map_override_chain", [])
    # Doubledowns are journaled even without a live bet session: their
    # window (the powerup timer) no longer requires one.
    double_downs = sorted(getattr(bot, "double_downs", set()))
    try:
        if session is None and not chain and not double_downs:
            coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
            return
        coin_escrow.update_one(
            {"_id": _ESCROW_DOC_ID},
            {
                "$set": {
                    "data": {
                        "bets": session["bets"] if session else {},
                        "open": bool(session.get("open")) if session else False,
                        "double_downs": double_downs,
                        "map_overrides": [dict(entry) for entry in chain],
                    }
                }
            },
            upsert=True,
        )
    except Exception as e:
        log.warning("Could not persist Duck Coin escrow journal: %s", e)


def clear_escrow_journal(bot) -> None:
    """Delete the journal after settle/refund/cancel; failures are harmless
    (a stale journal can only cause an extra startup refund, never a loss)."""
    session = getattr(bot, "bet_session", None)
    bot.bet_session = None
    if session:
        _cancel_window_tasks(session)
    try:
        coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
    except Exception as e:
        log.warning("Could not clear Duck Coin escrow journal: %s", e)


# Process-wide flag: recovery must run once per process, not once per
# on_ready (which Discord fires again after every gateway reconnect).
_escrow_recovered = False


def recover_orphaned_escrow(bot) -> None:
    """Refund bets, doubledowns, and map overrides journaled by a previous
    bot run.

    Runs exactly once per process, on startup: if the process died while
    coins were escrowed (open bet window), doubled down, or spent on a map
    override, those coins are returned here — settlement/reporting can never
    happen after a restart, so refund is the only fair outcome. No-op when
    there is no journal (fresh database) or when already recovered (gateway
    reconnects re-fire on_ready).
    """
    global _escrow_recovered
    if _escrow_recovered:
        return
    try:
        doc = coin_escrow.find_one({"_id": _ESCROW_DOC_ID})
    except Exception as e:
        log.warning("Could not read Duck Coin escrow journal: %s", e)
        return
    if not doc or "data" not in doc:
        _escrow_recovered = True
        return
    data = doc["data"] or {}
    bets = data.get("bets") or {}
    refunded = 0
    for side_bets in bets.values():
        for pid, amount in side_bets.items():
            add_coins(pid, amount)
            refunded += amount
    dd_refunded = 0
    for pid in data.get("double_downs") or []:
        add_coins(pid, DOUBLEDOWN_COST)
        dd_refunded += DOUBLEDOWN_COST
    # Refund the full map-override escalation chain to each payer (issue #195).
    overrides_refunded = 0
    for entry in data.get("map_overrides") or []:
        try:
            pid = str(entry["payer"])
            amount = int(entry["amount"])
        except (KeyError, TypeError, ValueError):
            log.warning("Skipping malformed map-override journal entry: %r", entry)
            continue
        add_coins(pid, amount)
        overrides_refunded += amount
    try:
        coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
    except Exception as e:
        log.warning("Could not clear Duck Coin escrow journal after recovery: %s", e)
    _escrow_recovered = True
    total = refunded + dd_refunded + overrides_refunded
    if total:
        log.warning(
            "Recovered %s escrowed Duck Coin(s) from before the last restart "
            "(%s bet coin(s), %s doubledown coin(s), %s map-override coin(s)); "
            "refunded to players",
            total,
            refunded,
            dd_refunded,
            overrides_refunded,
        )


def command_available(
    bot, *, requires_running_match: bool = True, channel=None
) -> str | None:
    """None when a Duck Coins command may run, else the rejection message.

    `!setmap` overrides the map during the captains draft, i.e. *before* the
    match is running, so it passes requires_running_match=False. `!bet` and
    `!doubledown` only make sense while a match is in progress.

    `!setmap` and `!doubledown` additionally pass `channel=ctx.channel`: both
    are powerups for the players in the current match, so they may only be
    used inside the generated match-#### channel. `!bet` never passes a
    channel — spectators bet from #10-mans or wherever they are. When no
    match channel exists (e.g. a simulate run) there is nothing to enforce.
    """
    if not duck_coins_enabled():
        return "Duck Coins features are disabled."
    if requires_running_match and not bot.match_ongoing:
        return "No match is running right now."
    if channel is not None:
        match_channel = getattr(bot, "match_channel", None)
        if match_channel is not None and getattr(channel, "id", None) != getattr(
            match_channel, "id", None
        ):
            name = getattr(match_channel, "name", None) or getattr(
                bot, "match_name", "match"
            )
            return f"Use this command in the `{name}` match channel."
    return None


def duck_emote(bot) -> str:
    """The custom :duckcoin: emote, or a duck fallback when it can't be found."""
    try:
        emoji = discord.utils.get(list(bot.emojis), name="duckcoin")
    except (AttributeError, TypeError):
        emoji = None
    return str(emoji) if emoji else "🦆"


def coins_of(player_id) -> int:
    doc = mmr_collection.find_one({"player_id": str(player_id)})
    return int(doc.get("duck_coins", 0)) if doc else 0


def add_coins(player_id, amount: int) -> None:
    mmr_collection.update_one(
        {"player_id": str(player_id)},
        {
            "$inc": {"duck_coins": amount},
            "$setOnInsert": {"player_id": str(player_id)},
        },
        upsert=True,
    )


def clear_season_coin_state(bot) -> None:
    """Drop per-match Duck Coin state on a season reset.

    Used by every season reset (!newseason, !resetseason) alongside zeroing
    balances, because in-memory state would otherwise leak old-season coins
    into the new season: escrowed bet coins would pay out at the next
    !report, stale doubledowns would still apply, and the map-override
    escalation would carry over. Open bets are dropped, not refunded, since
    coins reset anyway; the escrow journal is wiped too so a subsequent
    restart never refunds coins that a reset already voided.
    """
    session = getattr(bot, "bet_session", None)
    bot.bet_session = None
    if session:
        _cancel_window_tasks(session)
        log.info("Cleared open bet window for the season reset")
    bot.double_downs = set()
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.map_override_deadline = None
    bot.map_override_chain = []
    try:
        coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
    except Exception as e:
        log.warning("Could not clear Duck Coin escrow journal: %s", e)


def reset_all_coins() -> None:
    """Zero every player's coin balance (season reset)."""
    mmr_collection.update_many({}, {"$set": {"duck_coins": 0}})


def award_match_coins(player_ids) -> None:
    for pid in player_ids:
        add_coins(pid, 1)
    if player_ids:
        log.info("Awarded 1 Duck Coin to %s match players", len(player_ids))


def insufficient(bot, balance: int, needed: int) -> str:
    e = duck_emote(bot)
    return f"You have {balance} {e} but need {needed} {e} for that."


def open_map_override_grace(bot) -> None:
    """Open the 2-minute powerup window after teams finalize (issue #195).

    This deadline gates BOTH !setmap and !doubledown, so both commands stay
    available exactly as long as the powerup countdown in the match channel.
    """
    bot.map_override_deadline = time.monotonic() + SETMAP_GRACE_SECONDS
    log.info(
        "!setmap and !doubledown window open for %ss after team finalization",
        SETMAP_GRACE_SECONDS,
    )


def _in_grace_window(bot) -> bool:
    """True when match is ongoing and the 2-minute setmap grace is still open."""
    if not bot.match_ongoing:
        return False
    deadline = getattr(bot, "map_override_deadline", None)
    return deadline is not None and time.monotonic() <= deadline


async def _refresh_teams_embed(bot, new_map: str) -> None:
    """Retitle the posted teams embed after a grace-window map override."""
    message = getattr(bot, "current_teams_message", None)
    if message is None:
        return
    try:
        embed = message.embeds[0]
        embed.title = f"Teams on {new_map}"
        await message.edit(embed=embed)
    except (discord.NotFound, discord.HTTPException, AttributeError, IndexError) as e:
        log.warning("Could not update teams embed after map override: %s", e)


def _match_players(bot) -> set[str]:
    return {str(p["id"]) for p in bot.team1 + bot.team2}


def _signup_players(bot) -> set[str]:
    """Everyone signed up for the current match.

    The queue holds all match players from signup through cleanup, so it
    covers the pre-teams draft window where !setmap is allowed. team1/team2
    are unioned in so the check also holds if the queue is ever cleared
    while a match is still running.
    """
    ids = {str(p["id"]) for p in getattr(bot, "queue", []) or []}
    ids |= {str(p["id"]) for p in getattr(bot, "team1", []) or []}
    ids |= {str(p["id"]) for p in getattr(bot, "team2", []) or []}
    return ids


def _side_name(side: str) -> str:
    return "Attackers" if side == "attackers" else "Defenders"


# Betting: parimutuel pools, twitch-prediction style. ponytail: floor() rounding
# dust (at most one coin per winning bettor) is not redistributed.


def _fmt_clock(seconds: int) -> str:
    """m:ss countdown display (e.g. 4:30)."""
    return f"{seconds // 60}:{seconds % 60:02d}"


def _powerups_announcement(bot, remaining: int) -> str:
    """Match-channel notice for the post-setup powerup window.

    Posted once teams are announced with a live 2-minute countdown (the same
    window as the !setmap grace), then edited to a closed state.
    """
    e = duck_emote(bot)
    if remaining:
        header = f"⚔️ **Powerups enabled for {_fmt_clock(remaining)}**"
    else:
        header = (
            "⌛ **Powerup window closed** — `!doubledown` and `!setmap` are locked."
        )
    return (
        f"{header}\n"
        f"`!doubledown` costs {DOUBLEDOWN_COST} {e} to double your MMR change for this match.\n"
        f"Override the chosen map with `!setmap <map> [amount]` — wager {SETMAP_BASE_COST}+ {e} "
        f"(outbid the last override) to swap the map."
    )


def _team_lines(bot, team) -> list[str]:
    """One display line per player: tracker link + MMR."""
    lines = []
    for p in team:
        ud = users.find_one({"discord_id": str(p["id"])})
        mmr = (
            getattr(bot, "player_mmr", {}).get(str(p["id"]), {}).get("mmr", DEFAULT_MMR)
        )
        lines.append(f"{display_line_for(ud)} (MMR:{mmr})")
    return lines


def _betting_embed(bot, session, remaining: int) -> discord.Embed:
    """The live #10-mans betting embed: teams+MMR, pools, odds, countdown."""
    e = duck_emote(bot)
    atk_pool = sum(session["bets"]["attackers"].values())
    def_pool = sum(session["bets"]["defenders"].values())
    total = atk_pool + def_pool

    if remaining:
        opener = (
            f"{e} **Betting is open for the match below!** Bet with `!bet attackers <amount>` "
            f"or `!bet defenders <amount>` (min 1). Players in this match cannot bet."
        )
    else:
        opener = f"{e} **Betting is closed.**"
    if total:
        opener += f"\n\nTotal wagered: **{total}** {e}"

    title = f"{e} Duck Coin Betting — {getattr(bot, 'match_name', 'match')}"
    selected_map = getattr(bot, "selected_map", None)
    if selected_map:
        title += f" on {selected_map}"

    def team_value(team, pool):
        value = "\n".join(_team_lines(bot, team)) or "—"
        if pool and total:
            # Parimutuel: every coin on a side pays total/side when it wins.
            value += f"\n**Pool:** {pool} {e} — pays **{total / pool:.2f}x** per coin\n"
        else:
            value += f"\n**Pool:** {pool} {e}\n"
        return value

    embed = discord.Embed(title=title, description=opener, color=discord.Color.gold())
    embed.add_field(
        name="⚔️ Attackers", value=team_value(bot.team1, atk_pool), inline=False
    )
    embed.add_field(
        name="🛡️ Defenders", value=team_value(bot.team2, def_pool), inline=False
    )
    if remaining:
        embed.set_footer(text=f"Betting closes in {_fmt_clock(remaining)}")
    else:
        embed.set_footer(text="Betting closed.")
    return embed


def _ten_mans_channel(ctx):
    """The persistent #10-mans channel, falling back to ctx.channel."""
    guild = getattr(ctx, "guild", None)
    if guild is not None:
        try:
            channel = discord.utils.get(guild.text_channels, name="10-mans")
        except AttributeError:
            channel = None
        if channel is not None:
            return channel
    return getattr(ctx, "channel", None)


async def _edit_embed(session, embed) -> None:
    message = session.get("message")
    if message is None:
        return
    try:
        await message.edit(embed=embed)
    except (discord.NotFound, discord.HTTPException, AttributeError):
        pass


def _cancel_window_tasks(session) -> None:
    """Stop the bet-window and powerup countdown tasks for this session."""
    for key in ("task", "powerup_task"):
        task = session.get(key)
        if task:
            task.cancel()


def _schedule_betting_refresh(bot, session) -> None:
    """Re-render the betting embed right after a bet so odds stay live."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # called outside a running loop (self-checks)
    loop.create_task(_refresh_betting_embed(bot, session))


async def _refresh_betting_embed(bot, session) -> None:
    remaining = 0
    if session.get("open") and session.get("ends_at"):
        remaining = max(0, int(session["ends_at"] - asyncio.get_event_loop().time()))
    await _edit_embed(session, _betting_embed(bot, session, remaining))


async def on_teams_announced(bot, ctx) -> None:
    """Open the 5-minute betting window and the 2-minute powerup window.

    Two surfaces, split by audience:
    - match-#### channel: the powerup notice (!doubledown / !setmap) with a
      live 2-minute countdown matching the map-override grace window.
    - #10-mans: the betting embed (teams + MMR, pools, odds, expected payout,
      5-minute countdown) where spectators watch and place !bet from.
    """
    if not duck_coins_enabled() or ctx is None:
        return
    # Never clobber a live escrow: opening a new window over an unfinished
    # one would silently void the previous bets' coins. Refund them first.
    if getattr(bot, "bet_session", None):
        log.warning("Bet window reopened with a live session; refunding it first")
        refund_open_bets(bot)
    log.info("Opening %ss Duck Coin bet window", BET_WINDOW_SECONDS)
    loop = asyncio.get_event_loop()
    session = {
        "open": True,
        "bets": {"attackers": {}, "defenders": {}},
        "message": None,
        "powerup_message": None,
        "task": None,
        "powerup_task": None,
        "ends_at": loop.time() + BET_WINDOW_SECONDS,
    }
    bot.bet_session = session
    persist_escrow(bot)

    # Powerup notice in the match channel (falls back to ctx.channel on the
    # simulate path, where no match channel exists).
    channel = getattr(bot, "match_channel", None) or ctx.channel
    try:
        session["powerup_message"] = await channel.send(
            _powerups_announcement(bot, SETMAP_GRACE_SECONDS)
        )
    except discord.HTTPException:
        pass
    session["powerup_task"] = asyncio.create_task(_powerup_countdown(bot, session))

    # Betting embed in #10-mans.
    betting_channel = _ten_mans_channel(ctx)
    if betting_channel is not None:
        try:
            session["message"] = await betting_channel.send(
                embed=_betting_embed(bot, session, BET_WINDOW_SECONDS)
            )
        except discord.HTTPException:
            session["message"] = None
    session["task"] = asyncio.create_task(_bet_window_countdown(bot, session))


async def _bet_window_countdown(bot, session) -> None:
    loop = asyncio.get_event_loop()
    try:
        for _ in range(BET_WINDOW_SECONDS // BET_TICK_SECONDS):
            await asyncio.sleep(BET_TICK_SECONDS)
            if bot.bet_session is not session:
                return
            remaining = max(0, int(session["ends_at"] - loop.time()))
            if remaining <= 0:
                session["open"] = False
                log.info("Duck Coin bet window closed")
                await _edit_embed(session, _betting_embed(bot, session, 0))
                return
            if not session["open"]:
                return
            await _edit_embed(session, _betting_embed(bot, session, remaining))
    except asyncio.CancelledError:
        pass


async def _powerup_countdown(bot, session) -> None:
    """Tick the match-channel powerup notice through its 2-minute window."""
    message = session.get("powerup_message")
    if message is None:
        return
    try:
        for tick in range(1, SETMAP_GRACE_SECONDS // POWERUP_TICK_SECONDS + 1):
            await asyncio.sleep(POWERUP_TICK_SECONDS)
            if bot.bet_session is not session:
                return
            # Prefer the real grace deadline once open_map_override_grace has
            # run; fall back to fixed-window math before it is set.
            deadline = getattr(bot, "map_override_deadline", None)
            if deadline is not None:
                remaining = max(0, int(deadline - time.monotonic()))
            else:
                remaining = max(0, SETMAP_GRACE_SECONDS - tick * POWERUP_TICK_SECONDS)
            if remaining <= 0:
                break
            try:
                await message.edit(content=_powerups_announcement(bot, remaining))
            except (discord.NotFound, discord.HTTPException, AttributeError):
                return
        if bot.bet_session is session:
            try:
                await message.edit(content=_powerups_announcement(bot, 0))
            except (discord.NotFound, discord.HTTPException, AttributeError):
                pass
    except asyncio.CancelledError:
        pass


def place_bet(bot, user_id: str, side: str, amount: int) -> str:
    """Returns the reply message; bet coins are escrowed immediately."""
    session = getattr(bot, "bet_session", None)
    if not session or not session["open"]:
        return "No betting window is open right now."
    side = (side or "").lower()
    if side not in session["bets"]:
        return "Pick a side: `!bet attackers <amount>` or `!bet defenders <amount>`."
    if str(user_id) in _match_players(bot):
        return "You can't bet on a match you're playing in."
    if amount < 1:
        return "Minimum bet is 1 coin."
    balance = coins_of(user_id)
    if balance < amount:
        return insufficient(bot, balance, amount)
    add_coins(user_id, -amount)
    session["bets"][side][str(user_id)] = (
        session["bets"][side].get(str(user_id), 0) + amount
    )
    persist_escrow(bot)
    # Live odds: re-render the #10-mans betting embed right away.
    _schedule_betting_refresh(bot, session)
    e = duck_emote(bot)
    pool = sum(session["bets"][side].values())
    log.info(
        "Bet placed by %s: %s coins on %s (pool: %s)",
        user_id,
        amount,
        _side_name(side),
        pool,
    )
    return f"Bet placed: {amount} {e} on {_side_name(side)} (pool: {pool} {e})."


def refund_open_bets(bot) -> None:
    """Return escrowed bets (e.g. on cancel); safe to call at any time.

    Always clears the crash-safety journal even when there is no session:
    after a match is reported/settled, a stale journal would otherwise
    refund bets or overrides from a match that actually happened.
    """
    session = getattr(bot, "bet_session", None)
    if session:
        _cancel_window_tasks(session)
        refunded = 0
        for side_bets in session["bets"].values():
            for pid, amount in side_bets.items():
                add_coins(pid, amount)
                refunded += 1
        log.info("Refunded %s open bet(s)", refunded)
    clear_escrow_journal(bot)


def refund_match_coins(bot) -> int:
    """Refund EVERY coin spent on the current match, in-memory only.

    Covers all three sinks: escrowed bets, doubledown purchases, and map
    override wagers. Called when a match is cancelled — since the match
    never happens, none of that coin should be lost. Doubledown refunds are
    what the player paid (DOUBLEDOWN_COST); map overrides refund the full
    escalation chain so the coins trace back to who paid what.

    Returns the total number of coins refunded (0 when nothing to refund).
    """
    total = 0

    # 1) Escrowed bets.
    session = getattr(bot, "bet_session", None)
    if session:
        _cancel_window_tasks(session)
        for side_bets in session["bets"].values():
            for pid, amount in side_bets.items():
                add_coins(pid, amount)
                total += int(amount)
    bot.bet_session = None

    # 2) Doubledown purchases.
    for pid in getattr(bot, "double_downs", set()):
        add_coins(pid, DOUBLEDOWN_COST)
        total += DOUBLEDOWN_COST
    bot.double_downs = set()

    # 3) Map override wagers: refund every step of the escalation chain to
    # the player who paid it. Losing an outbid wager is NOT refunded here
    # (that's the point of an outbid); only a cancelled match returns coins.
    for entry in getattr(bot, "map_override_chain", []):
        pid = entry.get("payer")
        amount = int(entry.get("amount", 0))
        if pid and amount:
            add_coins(pid, amount)
            total += amount
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.map_override_deadline = None
    bot.map_override_chain = []

    clear_escrow_journal(bot)

    if total:
        log.info("Refunded %s Duck Coin(s) for a cancelled match", total)
    return total


def announce_cancellation(bot, guild) -> None:
    """Post the refund notice in #10-mans (best effort, never raises)."""
    e = duck_emote(bot)
    channel = None
    if guild is not None:
        try:
            channel = discord.utils.get(guild.text_channels, name="10-mans")
        except AttributeError:
            channel = None
    if channel is None:
        return
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(
                channel.send(f"Match cancelled, duck coins {e} returned to all users.")
            )
    except (discord.HTTPException, RuntimeError):
        pass


async def announce_cancellation_async(bot, guild) -> None:
    """Async form of announce_cancellation for await-style callers."""
    e = duck_emote(bot)
    channel = None
    if guild is not None:
        try:
            channel = discord.utils.get(guild.text_channels, name="10-mans")
        except AttributeError:
            channel = None
    if channel is None:
        return
    try:
        await channel.send(f"Match cancelled, duck coins {e} returned to all users.")
    except (discord.HTTPException, AttributeError):
        pass


async def settle_bets(bot, winner: str):
    """Pay out the parimutuel pool; return the settlement summary embed.

    The embed summarizes EVERY bettor — winners with their payout and net
    gain, losers with their lost stake — and is posted by the caller
    (commands/report.py) right after the match results embed, so the
    wrap-up reads as one flow. Coins are credited here; posting the embed
    is display-only. Returns None when there is no bet session or nobody
    bet at all, so the caller skips posting an empty summary.
    """
    session = getattr(bot, "bet_session", None)
    if not session:
        return None
    _cancel_window_tasks(session)
    bets = session["bets"]
    winners = bets.get(winner, {})
    loser_side = "defenders" if winner == "attackers" else "attackers"
    losers = bets.get(loser_side, {})
    pool = sum(winners.values())
    total = pool + sum(losers.values())
    e = duck_emote(bot)

    if not winners and not losers:
        # Nobody bet at all — nothing to summarize or post.
        log.info("Duck Coin bet window closed with no bets; nothing to settle")
        clear_escrow_journal(bot)
        return None

    winner_rows = []
    for pid, amount in sorted(winners.items(), key=lambda item: -item[1]):
        payout = amount * total // pool
        add_coins(pid, payout)
        winner_rows.append((pid, amount, payout, payout - amount))
    if winners:
        log.info("Settled %s bets on %s (%s coin pool)", len(winners), winner, total)
    else:
        log.info("No winning bets on %s; %s coin pool unclaimed", winner, total)
    clear_escrow_journal(bot)

    parts = [f"{total} {e} total pool"]
    match_name = getattr(bot, "match_name", "")
    if match_name:
        parts.insert(0, f"`{match_name}`")
    if not winners:
        parts.append(
            f"No one bet on {_side_name(winner).lower()} — the {total} {e} "
            "pool goes unclaimed."
        )

    embed = discord.Embed(
        title=f"{e} Betting Results — {_side_name(winner)} won!",
        description=" — ".join(parts),
        color=discord.Color.gold(),
    )
    if winner_rows:
        winners_text = "\n".join(
            f"<@{pid}> bet {amount} {e} → won **{payout}** {e} (+{net})"
            for pid, amount, payout, net in winner_rows
        )
        embed.add_field(
            name=f"✅ Winners ({len(winner_rows)})",
            value=winners_text,
            inline=False,
        )
    if losers:
        losers_text = "\n".join(
            f"<@{pid}> bet {amount} {e} → lost {amount} {e}"
            for pid, amount in sorted(losers.items(), key=lambda item: -item[1])
        )
        embed.add_field(
            name=f"❌ Lost ({len(losers)})", value=losers_text, inline=False
        )
    embed.set_footer(
        text="Parimutuel payouts — each winner's share scales with their stake"
    )
    return embed


def doubledown(bot, user_id: str) -> str:
    """Spend 5 coins to double this match's MMR delta.

    Gated to the 2-minute powerup window after teams are announced — the
    same deadline as !setmap, so both commands match the match-channel
    powerup countdown.
    """
    deadline = getattr(bot, "map_override_deadline", None)
    if deadline is None:
        return "Doubledown is only available after teams are announced."
    if time.monotonic() > deadline:
        return "The doubledown window has timed out."
    if str(user_id) not in _match_players(bot):
        return "Only players in this match can double down."
    if str(user_id) in bot.double_downs:
        return "You already doubled down for this match."
    balance = coins_of(user_id)
    if balance < DOUBLEDOWN_COST:
        return insufficient(bot, balance, DOUBLEDOWN_COST)
    add_coins(user_id, -DOUBLEDOWN_COST)
    bot.double_downs.add(str(user_id))
    persist_escrow(bot)
    e = duck_emote(bot)
    log.info("Doubledown purchased by %s for %s coins", user_id, DOUBLEDOWN_COST)
    return f"Paid {DOUBLEDOWN_COST} {e} — your MMR change for this match is doubled!"


def doubledown_multiplier_of(bot, player_id) -> int:
    """2 if this player paid for doubledown this match, else 1."""
    return 2 if str(player_id) in bot.double_downs else 1


async def setmap_override(bot, user_id: str, map_name: str, amount: int = None) -> str:
    """Override the voted map.

    With `amount` unset the cost escalates by one coin over the last override
    (legacy behavior, min SETMAP_BASE_COST). With `amount` set the player pays
    exactly that much, which must beat the last override amount.
    """
    # Players only: overrides are a match powerup, checked FIRST so
    # non-players get the same rejection in every phase (draft window,
    # live grace, or stale post-match state).
    if str(user_id) not in _signup_players(bot):
        return "Only players in this match can override the map."
    if bot.chosen_mode not in ("Captains", "Balanced"):
        return (
            "Map overrides only work in Captains or Balanced mode, after map "
            "voting has finished."
        )
    if not bot.selected_map:
        return "Map overrides only work after map voting has finished."
    if bot.match_ongoing:
        deadline = getattr(bot, "map_override_deadline", None)
        if deadline is None:
            return "Map overrides only work before the teams are fully decided."
        if time.monotonic() > deadline:
            return "The map override window has timed out."
    wanted = (map_name or "").strip().lower()
    pool = get_standard_maps()
    canonical = next((m for m in pool if m.lower() == wanted), None)
    if not canonical:
        return f"`{map_name}` isn't in the All Maps pool. Choose one of: {', '.join(pool)}."
    if bot.map_override_last_by == str(user_id):
        return "Wait for another player to override before you override again."
    last = bot.map_override_last
    if amount is None:
        cost = SETMAP_BASE_COST if not last else last + 1
    else:
        if amount < SETMAP_BASE_COST:
            e = duck_emote(bot)
            return f"The minimum override wager is {SETMAP_BASE_COST} {e}."
        if amount <= last:
            e = duck_emote(bot)
            return (
                f"Another player already wagered {last} {e}; wager more to take "
                "the override."
            )
        cost = amount
    balance = coins_of(user_id)
    if balance < cost:
        return insufficient(bot, balance, cost)
    add_coins(user_id, -cost)
    bot.selected_map = canonical
    bot.map_override_last = cost
    bot.map_override_last_by = str(user_id)
    # Journal each step of the escalation chain so a cancel or crash can
    # refund every payer (issue #195). Outbid wagers stay journaled too:
    # they are only refunded on a cancelled match, never when simply outbid.
    bot.map_override_chain.append({"payer": str(user_id), "amount": cost})
    persist_escrow(bot)
    e = duck_emote(bot)
    log.info("%s paid %s coins to override the map to %s", user_id, cost, canonical)
    if _in_grace_window(bot):
        # The teams/match summary embed is already posted; retitle it so it
        # reflects the overridden map (issue #195).
        await _refresh_teams_embed(bot, canonical)
    return f"<@{user_id}> paid {cost} {e} — the map is now **{canonical}**!"
