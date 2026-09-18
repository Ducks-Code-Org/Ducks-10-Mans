"""Duck Coins currency: awarding, betting, doubledown, and map overrides (issue #34)."""

import asyncio
import logging

import discord

from database import coin_escrow, mmr_collection
from globals import feature_enabled
from services.maps_service import get_standard_maps

log = logging.getLogger(__name__)

BET_WINDOW_SECONDS = 300
DOUBLEDOWN_COST = 5
SETMAP_BASE_COST = 3
# After teams finalize (match_ongoing flips True), !setmap stays usable this
# long — a grace window for last-second map swaps in both modes.
SETMAP_GRACE_SECONDS = 120


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
    """Mirror the current bet session + doubledown set into Mongo.

    Best-effort: persistence failures are logged and swallowed so a transient
    DB hiccup can never break an in-progress bet or doubledown.
    """
    session = getattr(bot, "bet_session", None)
    try:
        if session is None:
            coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
            return
        coin_escrow.update_one(
            {"_id": _ESCROW_DOC_ID},
            {
                "$set": {
                    "data": {
                        "bets": session["bets"],
                        "open": bool(session.get("open")),
                        "double_downs": sorted(getattr(bot, "double_downs", set())),
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
    if session and session.get("task"):
        session["task"].cancel()
    try:
        coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
    except Exception as e:
        log.warning("Could not clear Duck Coin escrow journal: %s", e)


def recover_orphaned_escrow(bot) -> None:
    """Refund bets and doubledowns journaled by a previous bot run.

    Called once at startup: if the process died while coins were escrowed
    (open bet window) or doubled down, those coins are returned here —
    settlement can never happen after a restart, so refund is the only
    fair outcome. No-op when there is no journal (fresh database).
    """
    try:
        doc = coin_escrow.find_one({"_id": _ESCROW_DOC_ID})
    except Exception as e:
        log.warning("Could not read Duck Coin escrow journal: %s", e)
        return
    if not doc or "data" not in doc:
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
    try:
        coin_escrow.update_one({"_id": _ESCROW_DOC_ID}, {"$unset": {"data": ""}})
    except Exception as e:
        log.warning("Could not clear Duck Coin escrow journal after recovery: %s", e)
    total = refunded + dd_refunded
    if total:
        log.warning(
            "Recovered %s escrowed Duck Coin(s) from before the last restart "
            "(%s bet coin(s), %s doubledown coin(s)); refunded to players",
            total,
            refunded,
            dd_refunded,
        )


def command_available(bot, *, requires_running_match: bool = True) -> str | None:
    """None when a Duck Coins command may run, else the rejection message.

    `!setmap` overrides the map during the captains draft, i.e. *before* the
    match is running, so it passes requires_running_match=False. `!bet` and
    `!doubledown` only make sense while a match is in progress.
    """
    if not duck_coins_enabled():
        return "Duck Coins features are disabled."
    if requires_running_match and not bot.match_ongoing:
        return "No match is running right now."
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
    if session and session.get("task"):
        session["task"].cancel()
    if session:
        log.info("Cleared open bet window for the season reset")
    bot.double_downs = set()
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.map_override_deadline = None
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
    """Allow !setmap for SETMAP_GRACE_SECONDS after teams finalize (issue #195)."""
    bot.map_override_deadline = asyncio.get_event_loop().time() + SETMAP_GRACE_SECONDS
    log.info(
        "!setmap grace window open for %ss after team finalization",
        SETMAP_GRACE_SECONDS,
    )


def _in_grace_window(bot) -> bool:
    """True when match is ongoing and the 2-minute setmap grace is still open."""
    if not bot.match_ongoing:
        return False
    deadline = getattr(bot, "map_override_deadline", None)
    return deadline is not None and asyncio.get_event_loop().time() <= deadline


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


def _side_name(side: str) -> str:
    return "Attackers" if side == "attackers" else "Defenders"


# Betting: parimutuel pools, twitch-prediction style. ponytail: floor() rounding
# dust (at most one coin per winning bettor) is not redistributed.


def _announcement(bot, remaining: int) -> str:
    e = duck_emote(bot)
    window = (
        f"Window closes in **{remaining // 60}:{remaining % 60:02d}**."
        if remaining
        else "**Betting window closed.**"
    )
    return (
        f"{e} **Betting is open!** Bet with `!bet attackers <amount>` or "
        f"`!bet defenders <amount>` (min 1). Players in this match cannot bet.\n"
        f"`!doubledown` costs {DOUBLEDOWN_COST} {e} to double your MMR change for this match.\n"
        f"{window}"
    )


async def _edit_window(session, text: str) -> None:
    try:
        await session["message"].edit(content=text)
    except (discord.NotFound, discord.HTTPException, AttributeError):
        pass


async def on_teams_announced(bot, ctx) -> None:
    """Open the 5-minute betting/doubledown window after teams are posted."""
    if not duck_coins_enabled() or ctx is None:
        return
    log.info("Opening %ss Duck Coin bet window", BET_WINDOW_SECONDS)
    session = {
        "open": True,
        "bets": {"attackers": {}, "defenders": {}},
        "message": None,
        "task": None,
    }
    bot.bet_session = session
    persist_escrow(bot)
    try:
        session["message"] = await ctx.send(_announcement(bot, BET_WINDOW_SECONDS))
    except discord.HTTPException:
        return
    guild = getattr(ctx, "guild", None)
    announcements = (
        discord.utils.get(guild.text_channels, name="10-mans") if guild else None
    )
    if announcements is not None and announcements.id != ctx.channel.id:
        try:
            await announcements.send(_announcement(bot, BET_WINDOW_SECONDS))
        except discord.HTTPException:
            pass
    session["task"] = asyncio.create_task(_bet_window_countdown(bot, session))


async def _bet_window_countdown(bot, session) -> None:
    try:
        for elapsed in range(30, BET_WINDOW_SECONDS + 1, 30):
            await asyncio.sleep(30)
            if bot.bet_session is not session:
                return
            remaining = BET_WINDOW_SECONDS - elapsed
            if remaining <= 0:
                session["open"] = False
                log.info("Duck Coin bet window closed")
                await _edit_window(session, _announcement(bot, 0))
                return
            if not session["open"]:
                return
            await _edit_window(session, _announcement(bot, remaining))
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
    """Return escrowed bets (e.g. on cancel); safe to call at any time."""
    session = getattr(bot, "bet_session", None)
    if not session:
        return
    if session.get("task"):
        session["task"].cancel()
    refunded = 0
    for side_bets in session["bets"].values():
        for pid, amount in side_bets.items():
            add_coins(pid, amount)
            refunded += 1
    log.info("Refunded %s open bet(s)", refunded)
    clear_escrow_journal(bot)


async def settle_bets(bot, channel, winner: str) -> None:
    """Pay out the parimutuel pool to bettors on the winning team."""
    session = getattr(bot, "bet_session", None)
    if not session:
        return
    if session.get("task"):
        session["task"].cancel()
    bets = session["bets"]
    winners = bets.get(winner, {})
    loser_side = "defenders" if winner == "attackers" else "attackers"
    pool = sum(winners.values())
    total = pool + sum(bets[loser_side].values())
    e = duck_emote(bot)
    if not winners:
        log.info("No winning bets on %s; %s coin pool unclaimed", winner, total)
        await channel.send(
            f"No one bet on {winner} — the {total} {e} pool goes unclaimed."
        )
        clear_escrow_journal(bot)
        return
    lines = []
    for pid, amount in sorted(winners.items(), key=lambda item: -item[1]):
        payout = amount * total // pool
        add_coins(pid, payout)
        lines.append(f"<@{pid}> bet {amount} → wins **{payout}** {e}")
    log.info("Settled %s bets on %s (%s coin pool)", len(winners), winner, total)
    clear_escrow_journal(bot)
    embed = discord.Embed(
        title=f"{_side_name(winner)} won! ({total} {e} pool)",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await channel.send(embed=embed)


def doubledown(bot, user_id: str) -> str:
    """Spend 5 coins to double this match's MMR delta; window-gated."""
    session = getattr(bot, "bet_session", None)
    if not session or not session["open"]:
        return (
            "Doubledown is only available in the 5 minutes after teams are announced."
        )
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
    if bot.chosen_mode not in ("Captains", "Balanced"):
        return (
            "Map overrides only work in Captains or Balanced mode, after map "
            "voting has finished."
        )
    if not bot.selected_map:
        return "Map overrides only work after map voting has finished."
    if bot.match_ongoing:
        deadline = getattr(bot, "map_override_deadline", None)
        if deadline is None or asyncio.get_event_loop().time() > deadline:
            return "Map overrides only work before the teams are fully decided."
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
    e = duck_emote(bot)
    log.info("%s paid %s coins to override the map to %s", user_id, cost, canonical)
    if _in_grace_window(bot):
        # The teams/match summary embed is already posted; retitle it so it
        # reflects the overridden map (issue #195).
        await _refresh_teams_embed(bot, canonical)
    return f"<@{user_id}> paid {cost} {e} — the map is now **{canonical}**!"
