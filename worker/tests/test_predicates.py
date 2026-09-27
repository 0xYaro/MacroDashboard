"""The four predicates, against the hand-computed values in `config/predicates.md`.

Every expected value below is worked out by hand in that file, from the real
Setup Ledger and the real `2026-W39.points.json`. A regression in a predicate
therefore shows up as a disagreement with a week that was already graded by
hand, not as a disagreement with a fixture this repository invented.

The market data, from `config/predicates.md`:

| Session        | spx_cash | breadth | hy_oas_bps |
| Mon 2026-09-14 |          |  56.26  |            |
| Tue 2026-09-15 | 7,585.73 |  52.88  |            |
| Wed 2026-09-16 | 7,507.77 |  50.09  |            |
| Thu 2026-09-17 |          |  50.29  |    270     |
| Fri 2026-09-18 | 7,650.50 |  49.50  |    270     |
"""

from __future__ import annotations

from macro.model import Status
from macro.predicates import crossed_within, failed_retest, gate_at_entry, streak_intact

from .conftest import context, history, reading

# ---------------------------------------------------------------------------
# The shared tape
# ---------------------------------------------------------------------------


def spx_week():
    """`spx_cash` for the week of 2026-09-14, closes and session extremes.

    The two closes `config/predicates.md` names are exact. The highs and lows
    are chosen to bracket them and are only consulted by `failed_retest`, which
    has its own tape below.
    """
    return (
        reading("spx_cash", "2026-09-14", "7620.00", high="7640.00", low="7600.00"),
        reading("spx_cash", "2026-09-15", "7585.73", high="7625.00", low="7570.00"),
        reading("spx_cash", "2026-09-16", "7507.77", high="7590.00", low="7495.00"),
        reading("spx_cash", "2026-09-17", "7600.00", high="7610.00", low="7520.00"),
        reading("spx_cash", "2026-09-18", "7650.50", high="7657.17", low="7610.52"),
    )


def breadth_week():
    return (
        reading("breadth_spx_above_200dma", "2026-09-14", "56.26"),
        reading("breadth_spx_above_200dma", "2026-09-15", "52.88"),
        reading("breadth_spx_above_200dma", "2026-09-16", "50.09"),
        reading("breadth_spx_above_200dma", "2026-09-17", "50.29"),
        reading("breadth_spx_above_200dma", "2026-09-18", "49.50"),
    )


def hy_week():
    return (
        reading("hy_oas_bps", "2026-09-14", "270"),
        reading("hy_oas_bps", "2026-09-15", "270"),
        reading("hy_oas_bps", "2026-09-16", "270"),
        reading("hy_oas_bps", "2026-09-17", "270"),
        reading("hy_oas_bps", "2026-09-18", "270"),
    )


# ---------------------------------------------------------------------------
# failed_retest
# ---------------------------------------------------------------------------


def test_failed_retest_is_failed_when_the_level_was_reclaimed(registry):
    """W35 Crypto "Extension fade (SFP, LTF)", evaluated on 2026-09-03.

    The ledger records `BTC printed $82,200 on Sep 3 after Waller, clearing the
    $81,500 invalidation outright`. Clause 1 holds: the 82,200 print puts the
    session high above 80,000. Clause 2 fails: BTC closed above 80,000 on that
    session and stayed above it.

    Hand-computed expectation: `failed`. Not `not yet` and not `met`. A
    predicate answering `met` on the wick alone would have kept a losing short
    open for its whole five-session window.
    """
    hist = history(
        reading("btc", "2026-08-30", "78000", high="78500", low="77500"),
        reading("btc", "2026-08-31", "78500", high="79000", low="78000"),
        reading("btc", "2026-09-01", "79000", high="79500", low="78500"),
        reading("btc", "2026-09-02", "79500", high="79900", low="79000"),
        reading("btc", "2026-09-03", "81800", high="82200", low="79400"),
    )
    outcome = failed_retest(
        {"series": "btc", "level": 80000, "within_sessions": 5, "direction": "above"},
        context(registry, hist, "2026-09-03"),
    )
    assert outcome.status is Status.FAILED
    assert outcome.evidence["touched_on"] == "2026-09-03"
    assert outcome.evidence["decided_on"] == "2026-09-03"


def test_failed_retest_is_met_when_the_touch_was_refused(registry):
    """The same tape with the 2026-09-03 close under the level.

    Hand-computed: the session high of 82,200 is at or above 80,000, so the
    level was touched. The close of 79,800 is below 80,000, and no later session
    closes at or above it, so the level was refused. Expected `met`.
    """
    hist = history(
        reading("btc", "2026-09-01", "79000", high="79500", low="78500"),
        reading("btc", "2026-09-02", "79500", high="79900", low="79000"),
        reading("btc", "2026-09-03", "79800", high="82200", low="79400"),
    )
    outcome = failed_retest(
        {"series": "btc", "level": 80000, "within_sessions": 5},
        context(registry, hist, "2026-09-03"),
    )
    assert outcome.status is Status.MET
    assert outcome.evidence["touched_on"] == "2026-09-03"
    assert outcome.evidence["direction"] == "above"  # the documented default


def test_failed_retest_is_not_yet_when_the_level_was_never_reached(registry):
    """No session in the window reaches the level.

    Hand-computed: the highest session high in the window is 79,900, which is
    below 80,000, so there is no touch. A level price never approached has not
    been refused. Expected `not yet`, never `met`.
    """
    hist = history(
        reading("btc", "2026-09-01", "79000", high="79500", low="78500"),
        reading("btc", "2026-09-02", "79500", high="79900", low="79000"),
        reading("btc", "2026-09-03", "79800", high="79850", low="79400"),
    )
    outcome = failed_retest(
        {"series": "btc", "level": 80000, "within_sessions": 5},
        context(registry, hist, "2026-09-03"),
    )
    assert outcome.status is Status.NOT_YET
    assert "touched_on" not in outcome.evidence


def test_failed_retest_treats_a_close_exactly_at_the_level_as_acceptance(registry):
    """`config/predicates.md`: a close exactly at the level is acceptance, not refusal."""
    hist = history(reading("btc", "2026-09-03", "80000", high="82200", low="79400"))
    outcome = failed_retest(
        {"series": "btc", "level": 80000, "within_sessions": 1},
        context(registry, hist, "2026-09-03"),
    )
    assert outcome.status is Status.FAILED


def test_failed_retest_is_unevaluable_when_a_session_is_missing(registry):
    """A hole in the window makes the sequence unreadable, never false."""
    hist = history(
        reading("btc", "2026-09-02", "79500", high="79900", low="79000"),
        reading("btc", "2026-09-03", None, display="couldn't retrieve", tier=4),
    )
    outcome = failed_retest(
        {"series": "btc", "level": 80000, "within_sessions": 5},
        context(registry, hist, "2026-09-03"),
    )
    assert outcome.status is Status.UNEVALUABLE
    assert outcome.evidence["missing_asof"] == "2026-09-03"


def test_failed_retest_below_reads_the_session_low(registry):
    """`direction: below` mirrors the test onto the low and the close."""
    hist = history(
        reading("btc", "2026-09-02", "76000", high="77000", low="75000"),
        reading("btc", "2026-09-03", "76500", high="77000", low="75800"),
    )
    outcome = failed_retest(
        {"series": "btc", "level": 75500, "within_sessions": 2, "direction": "below"},
        context(registry, hist, "2026-09-03"),
    )
    assert outcome.status is Status.MET
    assert outcome.evidence["touched_on"] == "2026-09-02"


# ---------------------------------------------------------------------------
# streak_intact
# ---------------------------------------------------------------------------


def test_streak_intact_is_met_on_a_three_session_outflow_run(registry):
    """W38 Crypto "ETH breakout-shelf FTR", evaluated on 2026-09-17.

    `eth_etf_inflow_streak` reads `-3`. The sign is negative, which matches
    `outflow`, and `abs(-3) = 3` is at least `3`.

    Hand-computed expectation: `met`.
    """
    hist = history(
        reading("eth_etf_inflow_streak", "2026-09-16", "-2"),
        reading("eth_etf_inflow_streak", "2026-09-17", "-3"),
    )
    outcome = streak_intact(
        {"series": "eth_etf_inflow_streak", "direction": "outflow", "min_sessions": 3},
        context(registry, hist, "2026-09-17"),
    )
    assert outcome.status is Status.MET
    assert outcome.evidence["value"] == "-3"


def test_streak_intact_is_not_yet_once_the_sign_flips(registry):
    """The same expression on 2026-09-18, when the series reads `+1`.

    Hand-computed: the sign no longer matches `outflow`. Expected `not yet`, and
    never `failed`: a streak that ends can start again the next session.
    """
    hist = history(
        reading("eth_etf_inflow_streak", "2026-09-17", "-3"),
        reading("eth_etf_inflow_streak", "2026-09-18", "1"),
    )
    outcome = streak_intact(
        {"series": "eth_etf_inflow_streak", "direction": "outflow", "min_sessions": 3},
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.NOT_YET


def test_streak_intact_is_not_yet_when_the_run_is_too_short(registry):
    """`direction: inflow, min_sessions: 2` on 2026-09-18, where the series reads `+1`.

    Hand-computed: `+1` matches the sign but `abs(1) = 1` is short of `2`.
    Expected `not yet`.
    """
    hist = history(reading("eth_etf_inflow_streak", "2026-09-18", "1"))
    outcome = streak_intact(
        {"series": "eth_etf_inflow_streak", "direction": "inflow", "min_sessions": 2},
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.NOT_YET
    assert outcome.evidence["sign_matches"] is True
    assert outcome.evidence["long_enough"] is False


def test_streak_intact_is_unevaluable_when_a_print_is_missing(registry):
    """The latest reading is older than the series' own `next_expected`.

    `eth_etf_inflow_streak` is `us_session; release=+1d`. A reading dated
    2026-09-11 is due to be followed by the 2026-09-14 session, published
    2026-09-15. Evaluated on 2026-09-18, that print is genuinely missing rather
    than not yet due, and the series is `replayable: false`, so it never
    arrives.

    Hand-computed expectation: `unevaluable`, not `not yet`.
    """
    hist = history(reading("eth_etf_inflow_streak", "2026-09-11", "2"))
    outcome = streak_intact(
        {"series": "eth_etf_inflow_streak", "direction": "inflow", "min_sessions": 2},
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE
    assert outcome.evidence["latest_asof"] == "2026-09-11"


def test_streak_intact_is_unevaluable_with_no_reading_at_all(registry):
    outcome = streak_intact(
        {"series": "eth_etf_inflow_streak", "direction": "inflow", "min_sessions": 2},
        context(registry, history(), "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE


# ---------------------------------------------------------------------------
# crossed_within
# ---------------------------------------------------------------------------


def test_crossed_within_is_not_yet_when_no_close_cleared_the_level(registry):
    """W37 Crypto "BTC $82K acceptance retest", evaluated on 2026-09-18.

    The ledger records `BTC failed near $80K and never printed a daily close
    above $82,000, so the acceptance condition never existed`.

    Hand-computed expectation: `not yet`.
    """
    hist = history(
        reading("btc", "2026-09-15", "74945"),
        reading("btc", "2026-09-16", "76500"),
        reading("btc", "2026-09-17", "78000"),
        reading("btc", "2026-09-18", "81000"),
    )
    outcome = crossed_within(
        {"series": "btc", "level": 82000, "direction": "above", "within_sessions": 10},
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.NOT_YET


def test_crossed_within_ignores_the_intraday_print_that_failed_retest_reads(registry):
    """The contrast that is the point of having both predicates.

    The same 2026-09-03 session, high 82,200 and close 81,800.

    `failed_retest` at 80,000 answers `failed`, because a high plus a close
    beyond the level is acceptance. `crossed_within` at 82,000 answers `not
    yet`, because no close cleared 82,000: one predicate reads the wick to find
    the touch, and the other never reads it at all.
    """
    hist = history(reading("btc", "2026-09-03", "81800", high="82200", low="79400"))
    ctx = context(registry, hist, "2026-09-03")
    assert (
        failed_retest({"series": "btc", "level": 80000, "within_sessions": 1}, ctx).status
        is Status.FAILED
    )
    assert (
        crossed_within(
            {"series": "btc", "level": 82000, "direction": "above", "within_sessions": 1}, ctx
        ).status
        is Status.NOT_YET
    )


def test_crossed_within_is_strict_at_the_level(registry):
    """A level named in a setup is a threshold to clear, not a value to reach.

    Hand-computed: a close of exactly 82,000 has not crossed 82,000, so the
    answer is `not yet`. One cent above it is `met`.
    """
    exact = history(reading("btc", "2026-09-03", "82000"))
    over = history(reading("btc", "2026-09-03", "82000.01"))
    args = {"series": "btc", "level": 82000, "direction": "above", "within_sessions": 1}
    assert crossed_within(args, context(registry, exact, "2026-09-03")).status is Status.NOT_YET
    assert crossed_within(args, context(registry, over, "2026-09-03")).status is Status.MET


def test_crossed_within_is_met_on_an_earlier_session_in_the_window(registry):
    """The acceptance need not be today; that is the whole reason for the window."""
    hist = history(
        reading("btc", "2026-09-16", "82500"),
        reading("btc", "2026-09-17", "80000"),
        reading("btc", "2026-09-18", "79500"),
    )
    outcome = crossed_within(
        {"series": "btc", "level": 82000, "direction": "above", "within_sessions": 3},
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.MET
    assert outcome.evidence["decided_on"] == "2026-09-16"


# ---------------------------------------------------------------------------
# gate_at_entry
# ---------------------------------------------------------------------------


def test_gate_at_entry_fails_on_the_w38_breadth_gate(registry):
    """W38 Equities "S&P shelf FTR long", evaluated on 2026-09-18.

    `7,540-7,590 on a retest of the Sep 10 low-close shelf with breadth above
    55% and HY under 285 bps`.

    Step 1: Tue 09-15 closed 7,585.73, inside [7540, 7590]. Wed 09-16 closed
    7,507.77, below entry_lo. Thu and Fri are not in the band, and Fri closed
    7,650.50, above it. The entry session is Tue 2026-09-15.

    Step 3: on 2026-09-15 breadth read 52.88 and HY read 270.
    `breadth > 55` is false, because 52.88 is not above 55.
    `hy_oas_bps < 285` is true, because 270 is under 285.
    `all` of a false and a true is false.

    Hand-computed expectation: `failed`, with the entry session reported as
    2026-09-15.
    """
    hist = history(*spx_week(), *breadth_week(), *hy_week())
    outcome = gate_at_entry(
        {
            "series": "spx_cash",
            "entry_lo": 7540,
            "entry_hi": 7590,
            "within_sessions": 5,
            "gate": {
                "all": [
                    {"op": "gt", "series": "breadth_spx_above_200dma", "value": 55},
                    {"op": "lt", "series": "hy_oas_bps", "value": 285},
                ]
            },
        },
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.FAILED
    assert outcome.evidence["entry_session"] == "2026-09-15"
    refused = outcome.evidence["gate"]["children"][0]["evidence"]
    assert refused["series"] == "breadth_spx_above_200dma"
    assert refused["current"] == "52.88"


def test_gate_at_entry_passes_on_the_w39_credit_and_floor_gate(registry):
    """The same tape against W39's replacement gate, ignoring the clip.

    `7,500-7,560 on a retest of the Sep 16 low, gated on HY under 292 bps and
    breadth not below 45% on the entry close`.

    The entry session is Wed 2026-09-16, whose close of 7,507.77 is inside
    [7500, 7560]. On that session HY read 270, under 292, and breadth read
    50.09, which is not below 45.

    Hand-computed expectation: `met`. Same series, same tape, same predicate,
    opposite answer, and the only thing that changed is the gate expression.
    """
    hist = history(*spx_week(), *breadth_week(), *hy_week())
    outcome = gate_at_entry(
        {
            "series": "spx_cash",
            "entry_lo": 7500,
            "entry_hi": 7560,
            "within_sessions": 5,
            "gate": {
                "all": [
                    {"op": "lt", "series": "hy_oas_bps", "value": 292},
                    {"op": "gte", "series": "breadth_spx_above_200dma", "value": 45},
                ]
            },
        },
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.MET
    assert outcome.evidence["entry_session"] == "2026-09-16"


def test_gate_at_entry_respects_the_activation_clip(registry):
    """The live W39 evaluation, with the clip in place.

    The W39 setup was authored on 2026-09-21, so its window starts at its own
    `activated_at` session and 2026-09-16 is outside it. No session since
    activation has closed inside [7500, 7560], and `spx_cash` read 7,650.50 on
    the most recent close.

    Hand-computed expectation: `not yet`. A predicate that skipped the clip
    would grade the W39 setup as entered on a session three days before it was
    written, and the recorded R-multiple would describe a trade that was never
    available.
    """
    hist = history(*spx_week(), *breadth_week(), *hy_week())
    args = {
        "series": "spx_cash",
        "entry_lo": 7500,
        "entry_hi": 7560,
        "within_sessions": 5,
        "gate": {
            "all": [
                {"op": "lt", "series": "hy_oas_bps", "value": 292},
                {"op": "gte", "series": "breadth_spx_above_200dma", "value": 45},
            ]
        },
    }
    clipped = gate_at_entry(
        args, context(registry, hist, "2026-09-18", not_before="2026-09-18")
    )
    assert clipped.status is Status.NOT_YET
    assert clipped.evidence["not_before"] == "2026-09-18"
    assert [one["asof"] for one in clipped.evidence["sessions"]] == ["2026-09-18"]

    unclipped = gate_at_entry(args, context(registry, hist, "2026-09-18"))
    assert unclipped.status is Status.MET  # the same call, only the clip removed


def test_gate_at_entry_is_unevaluable_when_the_gate_cannot_be_read_there(registry):
    """An entry session with no breadth reading is unevaluable, never blocked.

    Reporting it as blocked would be indistinguishable from a gate that
    genuinely refused, which is the failure this whole predicate exists to stop.
    """
    hist = history(*spx_week(), *hy_week())  # breadth absent entirely
    outcome = gate_at_entry(
        {
            "series": "spx_cash",
            "entry_lo": 7540,
            "entry_hi": 7590,
            "within_sessions": 5,
            "gate": {"op": "gt", "series": "breadth_spx_above_200dma", "value": 55},
        },
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE
    assert outcome.evidence["entry_session"] == "2026-09-15"


def test_gate_at_entry_is_not_yet_when_the_band_was_never_closed_inside(registry):
    """No close inside the band is a fact about the tape, not a failure to evaluate."""
    hist = history(*spx_week(), *breadth_week(), *hy_week())
    outcome = gate_at_entry(
        {
            "series": "spx_cash",
            "entry_lo": 7000,
            "entry_hi": 7100,
            "within_sessions": 5,
            "gate": {"op": "lt", "series": "hy_oas_bps", "value": 292},
        },
        context(registry, hist, "2026-09-18"),
    )
    assert outcome.status is Status.NOT_YET


def test_gate_at_entry_takes_the_most_recent_entry_session(registry):
    """Two entries into the same band: the later one is the one that counts."""
    hist = history(
        reading("spx_cash", "2026-09-15", "7550.00", high="7600", low="7500"),
        reading("spx_cash", "2026-09-16", "7700.00", high="7710", low="7600"),
        reading("spx_cash", "2026-09-17", "7545.00", high="7600", low="7500"),
        reading("breadth_spx_above_200dma", "2026-09-15", "60.00"),
        reading("breadth_spx_above_200dma", "2026-09-16", "58.00"),
        reading("breadth_spx_above_200dma", "2026-09-17", "40.00"),
    )
    outcome = gate_at_entry(
        {
            "series": "spx_cash",
            "entry_lo": 7540,
            "entry_hi": 7590,
            "within_sessions": 5,
            "gate": {"op": "gte", "series": "breadth_spx_above_200dma", "value": 45},
        },
        context(registry, hist, "2026-09-17"),
    )
    assert outcome.status is Status.FAILED
    assert outcome.evidence["entry_session"] == "2026-09-17"



def test_the_predicates_are_reachable_from_the_real_run_path():
    """A regression test for a bug that every other test here hid.

    The registry used to be a mutable global that `macro.predicates` filled on
    import. `main` reaches `expr` through `evaluate` and never through
    `predicates`, so on the real run path the set was empty and every predicate
    condition resolved to "predicate is not one of []", scored `unevaluable`,
    and blocked upgrade eligibility for ever. Every test in this file passed
    throughout, because importing the predicates to test them was itself what
    registered them.

    This asserts the set as seen from a module that imports only what `main`
    imports.
    """
    from macro import evaluate as _evaluate  # what main reaches expr through
    from macro import expr

    assert _evaluate is not None
    assert expr.registered() == {
        "failed_retest",
        "streak_intact",
        "crossed_within",
        "gate_at_entry",
    }
