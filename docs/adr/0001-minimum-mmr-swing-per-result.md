# Minimum MMR swing per result

Player-facing rating policy: winning a match should never cost rating, and every loss should always cost something. We decided to clamp the MMR delta by result — every win pays at least +5, every loss costs at least −5 — inside the shared delta formula, applied to the raw computed change before any multiplier (doubledown therefore doubles the clamped change: a floored win pays at least +10). The clamp is deliberately inside the one function every application path (report flow, revert tool) imports, so the paths can never drift.

The alternative — letting a heavy favorite's narrow win go MMR-negative (the old behaviour: the expectation and skill terms could outweigh the round-differential term) — punished players for winning, which surfaced as player complaints about losing MMR on wins. The cost is a softened signal: a favorite's blowout now pays the same minimum as a squeaker (the raw formula's near-neutral ≈ +0.48 becomes exactly +5), so MMR moves slightly less informatively at the extremes. We accepted that because the guarantee only ever binds on outcomes where the old behaviour was unfair or trivially small. The absolute 0 floor on total MMR is unchanged, so a loss near zero can still bite less than 5 in practice.

Ratings accrue under this policy, so reversing it later means a one-time rescale or an awkward correction — cheap to record now.
