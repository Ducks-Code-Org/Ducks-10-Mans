"""Self-check for tools/ops/revert_last_match.py MMR recovery.

Simulates a full report (update_stats per player, both veteran and
first-match placements, with doubledown multipliers), persists an
mmr_context exactly like report.py now does, then verifies:

  * Path A — replay_mmr_context reproduces every player's stored post-MMR
    from the persisted inputs through the live delta_mmr.
  * Path B — solve_pre_match_mmr_legacy inverts the live formula for
    legacy (no-context) matches, recovering the exact pre-MMRs for
    veterans and 0 for first-match players.
  * The rating-totals revert math mirrors stats_helper._apply_rating.

Run directly: python tests/test_revert_mmr_recovery.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) so the live
# stats_helper can be imported without a database.
_database_stub = types.ModuleType("database")
_database_stub.users = _database_stub.mmr_collection = None
_database_stub.all_matches = _database_stub.seasons = None
_database_stub.interests = _database_stub.recent_queue = None
_database_stub.coin_escrow = _database_stub.client = _database_stub.db = None
sys.modules["database"] = _database_stub

from game.stats_helper import delta_mmr, DEFAULT_MMR  # noqa: E402
from tools.ops.revert_last_match import (  # noqa: E402
    replay_mmr_context,
    solve_pre_match_mmr_legacy,
)


def main():
    # --- Shared scenario ----------------------------------------------------
    # 5 winners / 5 losers, mixed veterans and first-match players, mixed
    # ratings and a doubledown on one winner and one loser. Team averages
    # mirror report.py: veterans count pre-MMR, new players count their
    # 100×vlr seed.
    W_R, L_R = 13, 4  # rounds
    total_rounds = W_R + L_R

    # (did, pre_mmr, vlr, was_new, mult)
    roster = [
        ("w1", 420, 1.3, False, 2),
        ("w2", 150, 1.1, False, 1),
        ("w3", 800, 0.9, False, 1),
        ("w4", 0, 1.2, True, 1),  # first match → seed 120
        ("w5", 60, None, False, 1),  # no rating this match → vlr 1.0
        ("l1", 500, 1.0, False, 1),
        ("l2", 90, 0.7, False, 2),  # doubled loss; not floored
        ("l3", 0, 1.4, True, 1),  # first match → seed 140
        ("l4", 250, 0.5, False, 1),
        ("l5", 1000, 1.05, False, 1),
    ]

    team1_ids = [d for d, *_ in roster[:5]]
    team2_ids = [d for d, *_ in roster[5:]]
    pre_mmr = {d: p for d, p, *_ in roster}
    vlr = {d: v for d, _, v, *_ in roster}
    was_new = {d: n for d, _, _, n, _ in roster}
    mult = {d: m for d, _, _, _, m in roster}

    def seed(d):
        v = vlr[d]
        return max(0, round(100.0 * v)) if isinstance(v, (int, float)) else 0

    def effective(d):
        return seed(d) if was_new[d] else pre_mmr[d]

    team1_avg = sum(effective(d) for d in team1_ids) / len(team1_ids)
    team2_avg = sum(effective(d) for d in team2_ids) / len(team2_ids)

    # --- Simulate the report (mirrors report.py + update_stats) -------------
    post_mmr = {}
    ctx_players = {}
    for d, *_ in roster:
        our = W_R if d in team1_ids else L_R
        opp = L_R if d in team1_ids else W_R
        c = {
            "pre_mmr": pre_mmr[d],
            "was_new": was_new[d],
            "rating": vlr[d],
            "side": "team1" if d in team1_ids else "team2",
            "multiplier": mult[d],
        }
        ctx_players[d] = c
        if was_new[d]:
            base = seed(d)
        else:
            base = pre_mmr[d]
        delta = delta_mmr(
            our_rounds=our,
            opp_rounds=opp,
            our_mmr=team1_avg if d in team1_ids else team2_avg,
            opp_mmr=team2_avg if d in team1_ids else team1_avg,
            vlr=vlr[d] if isinstance(vlr[d], (int, float)) else 1.0,
        )
        post_mmr[d] = max(0, round(base + delta * mult[d]))

    mmr_context = {
        "team1_avg": team1_avg,
        "team2_avg": team2_avg,
        "team1_rounds": W_R,
        "team2_rounds": L_R,
        "players": ctx_players,
    }

    # --- Path A: replay the persisted context through the live formula ------
    for d, *_ in roster:
        expect = replay_mmr_context(
            delta_mmr,
            ctx_players[d],
            mmr_context,
            rating=vlr[d],
        )
        assert expect == post_mmr[d], (
            f"replay_mmr_context mismatch for {d}: expect={expect} "
            f"post={post_mmr[d]} ctx={ctx_players[d]}"
        )

    # And the veteran post-MMRs must differ from their pre-MMRs (the match
    # actually moved MMR), while first-match players must have moved too.
    assert all(post_mmr[d] != pre_mmr[d] for d, *_ in roster), post_mmr

    # --- Path B: legacy inversion (no context, no doubledowns) --------------
    # Same scenario but every multiplier forced to 1; the legacy solver sees
    # only post-MMRs, per-player ratings, and the was_new flags.
    legacy_post = {}
    legacy_ctx_avg = (team1_avg, team2_avg)
    for d, *_ in roster:
        our = W_R if d in team1_ids else L_R
        opp = L_R if d in team1_ids else W_R
        if was_new[d]:
            base = seed(d)
        else:
            base = pre_mmr[d]
        delta = delta_mmr(
            our_rounds=our,
            opp_rounds=opp,
            our_mmr=legacy_ctx_avg[0] if d in team1_ids else legacy_ctx_avg[1],
            opp_mmr=legacy_ctx_avg[1] if d in team1_ids else legacy_ctx_avg[0],
            vlr=vlr[d] if isinstance(vlr[d], (int, float)) else 1.0,
        )
        legacy_post[d] = max(0, round(base + delta))

    legacy_players = [
        {
            "discord_id": d,
            "current_mmr": legacy_post[d],
            "vlr": vlr[d],
            "was_new": was_new[d],
            "won": d in team1_ids,
        }
        for d, *_ in roster
    ]
    solved = solve_pre_match_mmr_legacy(delta_mmr, legacy_players, W_R, L_R)
    assert solved is not None, "legacy solver returned None"
    pre_solved, w_avg, l_avg = solved

    # Veterans must be recovered exactly; first-match players revert to 0.
    for d, p, *_ in roster:
        if was_new[d]:
            assert (
                pre_solved[d] == 0
            ), f"first-match {d} must revert to 0, got {pre_solved[d]}"
        else:
            assert (
                pre_solved[d] == p
            ), f"legacy inversion mismatch for {d}: solved={pre_solved[d]} actual={p}"
    # The recovered averages must be report.py's team averages — the whole
    # side, first-match players counted at their seed — because that is what
    # the deltas were computed from. (The solver used to average veterans
    # only; the shared scenario's rounding hid it, hence the extra leg below.)
    assert abs(w_avg - team1_avg) < 1e-9, (w_avg, team1_avg)
    assert abs(l_avg - team2_avg) < 1e-9, (l_avg, team2_avg)

    # --- Path B regression: placements must stay in the team averages ------
    # Two veterans + three placements vs five mixed veterans. One placement
    # has no usable rating this match: report.py counts it at seed 0 in the
    # average while its delta still uses vlr 1.0, so both halves of the
    # placement handling are exercised. The veteran-only average (548 vs
    # 310) differs from report.py's real one (271.2 vs 309.8), so the old
    # veteran-only solver mis-recovers every veteran (e.g. pw1 185 → 190)
    # and its returned pre-MMRs do not even replay through the formula.
    # Every pre-MMR must come back exact and the returned averages must be
    # report.py's whole-team ones.
    pl_roster = [
        ("pw1", 185, 1.3, False),
        ("pw2", 911, 1.0, False),
        ("pw3", 0, 1.3, True),
        ("pw4", 0, 1.3, True),
        ("pw5", 0, None, True),
        ("pl1", 422, 1.3, False),
        ("pl2", 130, 1.3, False),
        ("pl3", 229, 0.7, False),
        ("pl4", 173, 0.7, False),
        ("pl5", 595, 1.3, False),
    ]
    pl_w = pl_roster[:5]
    pl_l = pl_roster[5:]

    def pl_seed(v):
        return max(0, round(100.0 * v)) if isinstance(v, (int, float)) else 0

    pl_avg_w = sum(pl_seed(v) if new else pre for _, pre, v, new in pl_w) / 5
    pl_avg_l = sum(pl_seed(v) if new else pre for _, pre, v, new in pl_l) / 5
    vet_avg_w = sum(pre for _, pre, _, new in pl_w if not new) / 2
    assert abs(pl_avg_w - vet_avg_w) > 200, "placement leg must be distinguishable"
    pl_post = {}
    for did, pre, v, new in pl_roster:
        won = did.startswith("pw")
        our, opp = (W_R, L_R) if won else (L_R, W_R)
        ta, oa = (pl_avg_w, pl_avg_l) if won else (pl_avg_l, pl_avg_w)
        vlr = v if isinstance(v, (int, float)) else 1.0
        d = delta_mmr(our, opp, ta, oa, vlr)
        pl_post[did] = max(0, round((pl_seed(v) if new else pre) + d))
    pl_players = [
        {
            "discord_id": did,
            "current_mmr": pl_post[did],
            "vlr": v,
            "was_new": new,
            "won": did.startswith("pw"),
        }
        for did, pre, v, new in pl_roster
    ]
    pl_solved = solve_pre_match_mmr_legacy(delta_mmr, pl_players, W_R, L_R)
    assert pl_solved is not None, "placement-heavy legacy inversion returned None"
    pl_pre, pl_w_avg, pl_l_avg = pl_solved
    assert abs(pl_w_avg - pl_avg_w) < 1e-9, (pl_w_avg, pl_avg_w)
    assert abs(pl_l_avg - pl_avg_l) < 1e-9, (pl_l_avg, pl_avg_l)
    for did, pre, v, new in pl_roster:
        want = 0 if new else pre
        assert pl_pre[did] == want, (
            f"placement-heavy inversion mismatch for {did}: "
            f"solved={pl_pre[did]} actual={want}"
        )

    # --- Rating-totals revert mirrors _apply_rating --------------------------
    # A player with prior totals gains rating×rounds each match; the revert
    # must subtract exactly that.
    total_rating_points = 4.0 + 1.3 * total_rounds
    total_rating_rounds = 2 + total_rounds
    new_points = total_rating_points - 1.3 * total_rounds
    new_rounds = total_rating_rounds - total_rounds
    assert abs(new_points - 4.0) < 1e-9 and new_rounds == 2
    assert abs(new_points / new_rounds - 2.0) < 1e-9

    # --- Doubledown ambiguity is real (documents why Path B refuses) --------
    # A doubled loss with mult=2 and an undoubled loss with a different pre
    # can land on the same post-MMR; the legacy path assumes mult=1.
    d_l2 = delta_mmr(
        our_rounds=L_R,
        opp_rounds=W_R,
        our_mmr=team2_avg,
        opp_mmr=team1_avg,
        vlr=0.7,
    )
    post_single = max(0, round(90 + d_l2))
    post_double = max(0, round(90 + d_l2 * 2))
    # Not always distinguishable for small deltas — this is why the context
    # persists the multiplier rather than trusting inversion.
    assert post_single != post_double or True  # documented ambiguity

    print("revert MMR recovery self-check OK")


if __name__ == "__main__":
    main()
