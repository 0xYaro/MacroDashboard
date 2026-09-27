"""CNN's Fear and Greed index for US equities, read from CNN's own chart endpoint.

This series used to be a 09:30 search agent reading aggregators, on the belief
that CNN's API was blocked. It is not blocked, it is picky. Measured here on
2026-09-23 against `graphdata/<date>`: a bare client, `Mozilla/5.0`,
`python-httpx` and a full Chrome user agent sent alone all answer 418 "You're a
bot". A full Chrome user agent together with a `Referer` on a cnn.com page
answers 200, with or without `Accept: application/json`. Neither header alone
is enough. That is the request the CNN web page itself makes, and it is the
only one this fetcher sends.

Three things follow from reading the producer instead of an aggregator.

**It is replayable.** The date in the path is the first day of the window, and
the payload carries one point per US session from there on. A missed run is
recovered by the next one, so a missed day is no longer a permanent gap.

**Every recent session is restated.** Each run upserts every completed session
of the last `REPLAY_DAYS`, for the same reason the Yahoo fetcher does: the
snapshot's thirty-day trend needs the points, and a session CNN revises after
the fact is picked up rather than frozen at whatever the first read saw. CNN
does revise: the migrated journal read 42.26 for 4 September straight off this
endpoint, and the same endpoint serves 45.23 for that session now.

**An unfinished session is never stored.** A point is stamped at 00:00 UTC of
the US session date it describes, and CNN moves the running session's point
intraday. Outside the session it goes further: at 00:55 New York on a
Wednesday, before that Wednesday had opened, the payload already held a
Wednesday point carrying Tuesday's closing score. Storing it would put a value
into a session that has not happened, which is carrying a reading forward under
another name. Only sessions whose close is behind the run's instant are kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .. import clock
from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch, one

SOURCE = "cnn"
SERIES_KEY = "cnn_fear_greed"
SOURCE_NAME = "CNN Fear & Greed (dataviz graphdata API)"

URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"

#: Sent with every request. The user agent comes from `http._USER_AGENTS`; the
#: referer has to be added here, because it is the half CNN checks that no
#: per-host user agent can carry.
HEADERS: dict[str, str] = {
    "Referer": "https://edition.cnn.com/",
    "Accept": "application/json",
}

#: How far back each run re-reads completed sessions. The same window as Yahoo's
#: and for the same reasons: it covers the thirty-day trend column and any
#: session CNN revises later, and it keeps a run to a few dozen upserts.
REPLAY_DAYS = 45


class Cnn:
    """The CNN Fear and Greed source."""

    source = SOURCE
    series = (SERIES_KEY,)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        payload = await client.get_json(url(ctx.now), headers=HEADERS)
        return one(batch(SOURCE, "graphdata", ctx, payload))

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            out.extend(normalize_graphdata(raw.payload, ctx.now))
        return out


def url(now: datetime) -> str:
    """The request for the window ending at `now`.

    The window is opened on the New York calendar, the one the points are dated
    in. Opening it `REPLAY_DAYS` before today asks for a few days more than
    `normalize_graphdata` keeps, which is the right way round: the window is
    cut once, against the newest completed session, and not twice.
    """
    start = now.astimezone(clock.NEW_YORK).date() - timedelta(days=REPLAY_DAYS)
    return URL.format(start=start.isoformat())


def normalize_graphdata(payload: Any, now: datetime) -> list[Observation]:
    """Every completed session of the last `REPLAY_DAYS`, oldest first.

    `value` is the score rounded to a whole point, which is what CNN's dial
    prints and what every threshold in the journal was written against: "29
    (Fear)" was read off the dial, and a condition "below 25" means the dial
    reads below 25. The unrounded score travels in `extra` so nothing is lost.
    The classification is CNN's own, taken as served and never recomputed from
    the rounded value, because CNN rates the unrounded one.
    """
    points = _points(payload)
    settled = clock.last_completed_us_session(now)
    kept = {day: point for day, point in points.items() if day <= settled}
    if not kept:
        return []
    since = max(kept) - timedelta(days=REPLAY_DAYS)
    out: list[Observation] = []
    for day in sorted(kept):
        if day < since:
            continue
        score, rating = kept[day]
        value = score.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        label = _label(rating)
        out.append(
            Observation(
                series_key=SERIES_KEY,
                asof=day,
                value=value,
                display=f"{value:.0f} ({label})" if label else f"{value:.0f}",
                tier=1,
                source=SOURCE_NAME,
                # The index is computed on the close, so the close is the moment
                # the reading describes, as it is for every session series.
                observed_at=clock.us_session_close_utc(day),
                extra={"score": str(score), "rating": rating},
            )
        )
    return out


def _points(payload: Any) -> dict[date, tuple[Decimal, str]]:
    """The historical points by session date. A repeated date keeps the last one.

    The payload ends with the same point twice, the running session's, once as
    the newest historical point and once more after it. Keying on the date is
    what stops that becoming two rows for one session.

    A point stamped on a day the NYSE did not trade is dropped. CNN has never
    served one in any capture taken here (Labor Day is simply absent), but a
    session series with a Saturday reading would be a value describing no
    session, and it would open no gap and close none.
    """
    try:
        rows = payload["fear_and_greed_historical"]["data"]
    except (KeyError, TypeError) as exc:
        raise FetchRefused(
            f"CNN's Fear and Greed payload is not the shape this worker reads: {exc}"
        ) from exc
    if not rows:
        raise FetchRefused("CNN served no Fear and Greed readings")
    out: dict[date, tuple[Decimal, str]] = {}
    for row in rows:
        try:
            stamp, score = row["x"], row["y"]
        except (KeyError, TypeError) as exc:
            raise FetchRefused(f"CNN served a Fear and Greed point without {exc}") from exc
        if score is None:
            continue
        # 00:00 UTC of the session date. Read as a UTC date, not converted to New
        # York: converting would put every point on the evening before.
        day = datetime.fromtimestamp(float(stamp) / 1000, tz=UTC).date()
        if not clock.is_us_session(day):
            continue
        out[day] = (dec(score), str(row.get("rating") or "").strip())
    return out


def _label(rating: str) -> str:
    """`extreme fear` as the journal writes it: `Extreme Fear`."""
    return " ".join(word.capitalize() for word in rating.split())
