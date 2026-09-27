# Named path-dependent predicates

`docs/CONTRACT.md` fixes the condition expression form as a narrow typed JSON shape over named series, composed with `all`, `any` and `n_of`.
That shape can say what is true today.
It cannot say what happened over the last five sessions, and every phase tracker condition and setup gate written to date has a path in it.

These four predicates are the whole of the path-dependent vocabulary.
They live in `worker/src/macro/predicates.py`, which this file does not own.
This file owns the conventions those implementations must satisfy, and it is binding on them in the same way `docs/CONTRACT.md` is binding on the schema.

The initial set is fixed: `failed_retest`, `streak_intact`, `crossed_within`, `gate_at_entry`.
A fifth predicate is not added by writing one.
It is added by amending `docs/CONTRACT.md` and this file first, because an unlisted predicate is a condition nobody can verify by reading it.

## Rules every predicate obeys

**Closes decide, highs and lows only witness.**
`docs/DECISIONS.md` fixes setup evaluation on the close, because every entry, invalidation, gate and planned R:R in the Setup Ledger is written against closes and eighteen graded rows depend on that meaning holding still.
A predicate may read `session_high` and `session_low` from `macro_core.observation.extra` to answer "was this level reached", and it must read the close to answer "was this level accepted".
No predicate resolves true on an intraday print alone.

**A missing observation is `unevaluable`, never `false`.**
If any session inside a predicate's window has no observation for a series the predicate names, the predicate returns `unevaluable` with the series key and the missing `asof` in its evidence.
This is the same rule the expression form already follows, and it is why an unevaluable phase condition counts as neither holding nor failed.
Nothing is interpolated and no value is carried into a day it did not describe.

**A window is counted in sessions of the named series, not in calendar days.**
`within_sessions: 5` over `spx_cash` means the last five US sessions that series carries an observation for, ending at the evaluation date inclusive.
Over `btc` it means the last five calendar days, because `btc` advances on `calendar_day`.
A weekend is not a session and does not consume the window.

**A window never reaches back past the moment the judgment was authored.**
For a condition belonging to a setup, a phase call or a trigger clause, the window is clipped at that row's `activated_at` session.
A setup cannot have been entered before it existed, and a tracker condition cannot be met by a session that preceded the call it belongs to.
Without this clipping a setup proposed on Monday could grade as triggered on the prior Thursday's tape.

**Every argument is in the series' declared unit.**
`config/series.yaml` declares one canonical unit per series and a predicate argument is read in it.
`hy_oas_bps` takes `292`, never `2.92`.

**Every predicate returns its evidence.**
The return is a status plus a JSON object naming the sessions examined, the values read, and the one session the answer turned on.
`macro_mart.condition_state.evidence` carries it and the web app renders it, so a reader can see why a gate refused without opening the worker.

**Every predicate carries a unit test with a hand-computed expected value.**
The worked examples below are those values.
They are drawn from the real Setup Ledger and the real `2026-W39.points.json`, so a regression in a predicate shows up as a disagreement with a week that was already graded by hand.

### The market data these examples use

Every number below is from `2026-W39.points.json` or from the Setup Ledger's own grading notes.

| Session | `spx_cash` close | `breadth_spx_above_200dma` | `hy_oas_bps` |
| --- | --- | --- | --- |
| Mon 2026-09-14 | | 56.26 | |
| Tue 2026-09-15 | 7,585.73 | 52.88 | |
| Wed 2026-09-16 | 7,507.77 | 50.09 | |
| Thu 2026-09-17 | | 50.29 | 270 |
| Fri 2026-09-18 | 7,650.50 | 49.50 | 270 |

`eth_etf_inflow_streak` read `-3` on Wed 2026-09-17 and `+1` on Fri 2026-09-18.
`btc` never printed a daily close above 82,000 in the weeks W37 or W38, and printed 82,200 intraday on Wed 2026-09-03.

---

## `failed_retest`

**What it means.**
Price came back to a named level, touched it, and was refused there.

**Why it is a predicate and not an expression.**
"Rallies fail at prior support" is a statement about a sequence.
`{"op": "lt", "series": "spx_cash", "value": 7450}` says price is below 7450 today, which is also true of a market that collapsed through it a month ago and never looked back.

**Arguments.**

| Argument | Type | Required | Meaning |
| --- | --- | --- | --- |
| `series` | series key | yes | The series the level is a level of. |
| `level` | number, in the series' unit | yes | The level being retested. |
| `within_sessions` | int | yes | How many sessions back the touch may have occurred, ending at the evaluation date inclusive. |
| `direction` | `above` or `below` | no, default `above` | `above` means price approached the level from underneath and failed to accept over it. `below` mirrors it. |

`docs/CONTRACT.md`'s worked example omits `direction`, which is why it has a default rather than being required.

**Convention.**
Let `W` be the window: the last `within_sessions` sessions of `series` ending at the evaluation date, clipped at the owning judgment's `activated_at` session.
With `direction: above`, `failed_retest` is `met` when both of these hold.

1. Some session in `W` has `session_high >= level`.
   This is the touch.
2. No session in `W` at or after that touch has `close >= level`.
   This is the refusal.

With `direction: below`, read `session_low <= level` and `close <= level`.
If there is no touch in `W`, the result is `not yet`, because a level price never approached has not been refused.
If there is a touch and a later close beyond the level, the result is `failed`, because the level was accepted rather than refused, and the condition is not going to become true by waiting.
The distinction between `not yet` and `failed` is the point: a tracker that reports both as "not yet" hides the difference between a setup still waiting and a setup whose premise is gone.

Comparisons against `level` are inclusive on the touch and inclusive on the acceptance.
A close exactly at the level is acceptance, not refusal.

**Whether the level was previously traded through is not part of the test.**
The authoring agent names a level because of its history, and re-deriving that history inside the predicate would score the same judgment twice.

**Unevaluable when** any session in `W` lacks an observation for `series`, or lacks `session_high` (or `session_low`) in its `extra`.

**Worked example, from the Setup Ledger, W35 Crypto "Extension fade (SFP, LTF)".**

The setup reads `Only if $80,000 is reclaimed and rejected on a wick with funding above neutral`, and the rejection half is this predicate.

```jsonc
{"predicate": "failed_retest",
 "args": {"series": "btc", "level": 80000, "within_sessions": 5, "direction": "above"}}
```

The ledger's grading note is `BTC printed $82,200 on Sep 3 after Waller, clearing the $81,500 invalidation outright; the fade never reached $72,000`, and the row graded `triggered → stopped` at `−1.0R`.

Evaluated on 2026-09-03 with a five-session window:

- Clause 1 holds.
  The 82,200 print puts `session_high` above 80,000, so the level was touched.
- Clause 2 fails.
  BTC closed above 80,000 on that session and stayed above it, which the ledger records as clearing 81,500 outright.

**Expected: `failed`.**
Not `not yet`, and not `met`.
The level was reclaimed and held, so the fade's premise was gone the same day, and a predicate that answered `met` on the wick alone would have kept a losing short open for its full five-session window.

---

## `streak_intact`

**What it means.**
A run of same-signed sessions is still running, and is at least as long as the condition requires.

**The series it reads is a signed session count, not prose.**
`config/series.yaml` stores `btc_etf_inflow_streak` and `eth_etf_inflow_streak` as `numeric` in units of `sessions`.
`+2` is two consecutive inflow sessions, `-4` is four consecutive outflow sessions, `0` is a session whose net flow was exactly zero.
Since 2026-09-24 the worker computes both from the daily net flow series `btc_etf_net_flow` and `eth_etf_net_flow` (`worker/src/macro/fetchers/etf_flows.py`): the run is walked back over NYSE sessions, so a holiday never breaks it, and a session that breaks a run starts the next one at `+1` or `-1`.
The journal writes the same fact as a sentence and `display` keeps that sentence, but a `text` series may only appear in a `manual` condition, so a prose streak could never be machine-checked.

**Arguments.**

| Argument | Type | Required | Meaning |
| --- | --- | --- | --- |
| `series` | series key, unit `sessions` | yes | The streak series. |
| `direction` | `inflow` or `outflow` | yes | Which sign the run must carry. `inflow` is positive, `outflow` is negative. |
| `min_sessions` | int, at least 1 | yes | How long the run must be. |

There is no default for `min_sessions`.
A streak of unstated length is not a checkable condition.

**Convention.**
Read the latest observation of `series` whose `asof` is at or before the evaluation date.
`streak_intact` is `met` when the sign of `value` matches `direction` and `abs(value) >= min_sessions`.
It is `not yet` otherwise.
It never returns `failed`: a streak that ends can start again the next session, so there is no state from which the condition cannot become true.

**This predicate is not a flow threshold and must not be used as one.**
`ETH ETF flows positive on the week` is a statement about `eth_etf_weekly_flow`, and it is written as `{"op": "gt", "series": "eth_etf_weekly_flow", "value": 0}`.
Writing it as a streak asks a different question and gets a different answer, which is exactly what happened in W38.

**Unevaluable when** there is no observation at or before the evaluation date, or the latest observation's `asof` is older than the series' `next_expected`, which means a print is genuinely missing rather than merely not due yet.
The ETF streak series are `replayable: true` since they moved to the worker, so a missed session is a recoverable `macro_quality.gap` that closes when the next run re-states it from SoSoValue's history.
A session missing inside a run leaves the streak unknown rather than short: no row is written for it, and the condition reads the latest row before it.

**Worked example, from the Setup Ledger, W38 Crypto "ETH breakout-shelf FTR (FTR, HTF)".**

The setup reads `$2,400–$2,460 on a retest of the broken $2,500 shelf with ETH ETF flows positive on the week`.

The ledger's grading note is `ETH closed $2,399.09, $2,416.12 and $2,447.11 inside the band on Sep 15–17, but its ETFs ran outflows on exactly those three sessions and the week finished −$140.6M, so the flows-positive clause kept it shut. ETH then ran to $2,692, a forgone ~+1.7R`.

Two readings, on the two dates the ledger names.

```jsonc
{"predicate": "streak_intact",
 "args": {"series": "eth_etf_inflow_streak", "direction": "outflow", "min_sessions": 3}}
```

Evaluated on 2026-09-17, `eth_etf_inflow_streak` reads `-3`.
The sign is negative, which matches `outflow`, and `abs(-3) = 3` is at least `3`.
**Expected: `met`.**

Evaluated on 2026-09-18, the same expression reads `+1`.
The sign no longer matches.
**Expected: `not yet`.**

And with `direction: inflow, min_sessions: 2` on 2026-09-18, `+1` matches the sign but `abs(1) = 1` is short of `2`.
**Expected: `not yet`.**

The forgone 1.7R in that row came from writing a weekly flow threshold where a session streak belonged, not from the predicate.
The unit test exists so that the two stay distinguishable in code.

---

## `crossed_within`

**What it means.**
The series accepted beyond a level at least once in the recent past, whether or not it is beyond it now.

**Why it is a predicate and not an expression.**
`after a daily close above $82,000` is a precondition on a later entry.
By the time the entry band is reached, price is back under 82,000 by construction, so a plain `gt` against today's value is false exactly when the setup wants it true.

**Arguments.**

| Argument | Type | Required | Meaning |
| --- | --- | --- | --- |
| `series` | series key | yes | The series that must have crossed. |
| `level` | number, in the series' unit | yes | The level it must have crossed. |
| `direction` | `above` or `below` | yes | Which side of the level counts as crossed. |
| `within_sessions` | int | yes | How many sessions back the cross may have occurred, ending at the evaluation date inclusive. |

`direction` is required here, unlike in `failed_retest`.
An acceptance has no natural default side, and a setup that reads "after a daily close above" and one that reads "after a daily close below" are opposite trades.

**Convention.**
Let `W` be the window, defined and clipped exactly as in `failed_retest`.
`crossed_within` is `met` when some session in `W` has `close > level` for `direction: above`, or `close < level` for `direction: below`.
It is `not yet` otherwise.
It never returns `failed`, for the same reason `streak_intact` does not: another session can always cross.

**The comparison is strict, and the close is the only reading consulted.**
A level named in a setup is a threshold to clear, not a value to reach, so a close exactly at `level` has not crossed it.
This is the one place the convention differs from `between`, whose bounds are inclusive because a band is a region price occupies.
`session_high` and `session_low` are not read at all here.
An intraday print through a level is a print, and the ledger's own vocabulary calls acceptance a daily close.

**Unevaluable when** any session in `W` lacks an observation for `series`.

**Worked example, from the Setup Ledger, W37 Crypto "BTC $82K acceptance retest (FTR, HTF)".**

The setup reads `$79.5K–$81K after a daily close above $82,000, on a retest that holds with ETF flow still positive`, and the acceptance half is this predicate.

```jsonc
{"predicate": "crossed_within",
 "args": {"series": "btc", "level": 82000, "direction": "above", "within_sessions": 10}}
```

The ledger's grading note is `BTC failed near $80K and never printed a daily close above $82,000, so the acceptance condition never existed`, and the row graded `not triggered (W38)`.

Evaluated on 2026-09-18 over a ten-session window, no session in the window closed above 82,000.

**Expected: `not yet`.**

The same setup is re-proposed in W39 at a lower band, and `btc` read 81,492 on 2026-09-21, still under the level.
**Expected on 2026-09-21: `not yet`.**

The contrast with `failed_retest` is the point of having both.
The 82,200 intraday print on 2026-09-03 makes `failed_retest` at 80,000 answer `failed` for that week, because a high plus a close beyond the level is acceptance.
It does not make `crossed_within` at 82,000 answer `met`, because no close cleared 82,000.
One predicate reads the wick to find the touch; the other never reads it at all.

---

## `gate_at_entry`

**What it means.**
The gate clause held on the session the entry band was actually occupied, not on whatever session happens to be today.

**Why it exists.**
`README.md` opens with it.
Three consecutive weeks, W37, W38 and W39, a setup was blocked by a gate clause that was wrong, and each time the error was found the following Monday.
The mechanism was always the same: a gate is a statement about the moment of entry, and it was being read as a statement about the present.
A gate re-read today lets a setup that was correctly refused on Tuesday open on Friday on unrelated news.
A gate read only today misses an entry that was entirely valid on Tuesday and has since passed.
Neither is what the setup meant.

**Arguments.**

| Argument | Type | Required | Meaning |
| --- | --- | --- | --- |
| `series` | series key | yes | The series the entry band is measured on. For an equity setup this is always a cash index, never a front-month future. |
| `entry_lo` | number, in the series' unit | yes | Bottom of the entry band. |
| `entry_hi` | number, in the series' unit | yes | Top of the entry band. |
| `within_sessions` | int | yes | How far back to look for the entry session. |
| `gate` | condition expression | yes | Any expression the form allows: an `op`, an `all`, an `any`, an `n_of`, or another predicate. |

`gate` is a nested expression rather than a flat list of comparisons, so a gate can say `n_of 2 of 3` without this predicate growing an argument for it.

**Convention.**
Let `W` be the window, defined and clipped exactly as in `failed_retest`.

1. Find the **entry session**: the most recent session in `W` whose `close` for `series` lies inside `[entry_lo, entry_hi]`, inclusive at both ends.
2. If no session in `W` closed inside the band, return `not yet`.
   The entry never happened, and that is a fact about the tape, not a failure to evaluate.
3. Otherwise evaluate `gate` against the observations whose `asof` is the entry session.
   Every series the gate names is read at that `asof` and at no other.
4. Return `met` if the gate holds there and `failed` if it does not.

`failed` rather than `not yet` at step 4 is deliberate.
The entry session has passed and its readings will not change, so the gate's verdict on that entry is final.
A later entry into the same band is a different entry session and is evaluated on its own readings.

**The band is tested on the close, and the band-touch flag is a separate reading.**
`macro_mart.setup_state` carries `band_touched_today` and `band_touched_close` as two columns because they are two facts.
This predicate consults the close only.
A session whose low reached into the band but whose close did not is not an entry session here, and it raises `band_touched_today` so that "entry band reached, gate refused, trade forgone" surfaces the same day instead of the following Monday.

**Unevaluable when** an entry session exists but any series the gate names has no observation for that `asof`, or carries a `macro_quality.gap` there.
A gate whose breadth reading is missing for the entry session is unevaluable, and the setup reports `gate_state: unevaluable` rather than `blocked`.
Reporting it as `blocked` would be indistinguishable from a gate that genuinely refused.

**Worked example, from the Setup Ledger, W38 Equities "S&P shelf FTR long (FTR, HTF)".**

The setup reads `7,540–7,590 on a retest of the Sep 10 low-close shelf with breadth above 55% and HY under 285 bps`.

```jsonc
{"predicate": "gate_at_entry",
 "args": {"series": "spx_cash",
          "entry_lo": 7540, "entry_hi": 7590,
          "within_sessions": 5,
          "gate": {"all": [{"op": "gt", "series": "breadth_spx_above_200dma", "value": 55},
                           {"op": "lt", "series": "hy_oas_bps",               "value": 285}]}}}
```

The ledger's grading note is `Price closed 7,585.73 inside the band on Sep 15 and traded through it to 7,507.77 on Sep 16, but breadth was 52.88% and 50.09% on those closes against the above-55% gate; HY at 270 bps passed and the 7,470 invalidation never closed`, and the row graded `not triggered (W39)`.

Evaluated on 2026-09-18 with a five-session window covering Mon 09-14 through Fri 09-18:

- Step 1.
  Tue 09-15 closed 7,585.73, which is inside `[7540, 7590]`.
  Wed 09-16 closed 7,507.77, which is below `entry_lo`.
  Thu 09-17 and Fri 09-18 are not in the band either, and Fri closed 7,650.50, above it.
  The entry session is **Tue 2026-09-15**.
- Step 3.
  On 2026-09-15, `breadth_spx_above_200dma` read 52.88 and `hy_oas_bps` read 270.
- `breadth > 55` is false, because 52.88 is not above 55.
- `hy_oas_bps < 285` is true, because 270 is under 285.
- `all` of a false and a true is false.

**Expected: `failed`, with the entry session reported as 2026-09-15 and `breadth_spx_above_200dma = 52.88` named as the clause that refused.**

**The same example against W39's replacement gate.**

W39 replaced the rising-breadth clause with a credit-and-floor clause after three straight weeks of it self-contradicting, and the W39 row reads `7,500–7,560 on a retest of the Sep 16 low, gated on HY under 292 bps and breadth not below 45% on the entry close`.

```jsonc
{"predicate": "gate_at_entry",
 "args": {"series": "spx_cash",
          "entry_lo": 7500, "entry_hi": 7560,
          "within_sessions": 5,
          "gate": {"all": [{"op": "lt",  "series": "hy_oas_bps",               "value": 292},
                           {"op": "gte", "series": "breadth_spx_above_200dma", "value": 45}]}}}
```

Run against the same tape, ignoring the activation clip for a moment, the entry session is **Wed 2026-09-16**, whose close of 7,507.77 is inside `[7500, 7560]`.
On that session `hy_oas_bps` read 270, which is under 292, and `breadth_spx_above_200dma` read 50.09, which is not below 45.

**Expected: `met`.**

Same series, same tape, same predicate, opposite answer, and the only thing that changed is the gate expression.
That is the whole argument for the W39 rewrite and it is worth a unit test of its own.

The clip matters in the live case and must be in the test.
The W39 setup was authored on 2026-09-21, so its window starts at its own `activated_at` session and 2026-09-16 is outside it.
**Expected in the live W39 evaluation on 2026-09-21: `not yet`**, because no session since the setup was activated has closed inside `[7500, 7560]`, and `spx_cash` read 7,650.50 on the most recent close.
A predicate that skipped the clip would grade the W39 setup as entered on a session three days before it was written.
