"""The cutover switch: a feed the desk writes is not read from its Obsidian note.

`MACRO_FEED_SOURCE_CATALYSTS`, `_TAIL_RISKS` and `_HOLDINGS` are each `vault`
(the default) or `desk`. In desk mode the note is never opened, yields nothing
and is never whole, so it cannot retire a catalyst, retire a risk or deactivate
a holding that the desk wrote. docs/DECISIONS.md 2026-09-24.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from macro import config, vault

CATALYSTS = """# Catalyst Monitor

## Tracked Catalysts

- Catalyst: U.S. August CPI. Date & Time: 2026-10-14 08:30 ET / 2026-10-14 20:30 SGT. \
Source: BLS https://www.bls.gov/cpi. Affected: rates, USD.

## Newly Added
"""


def test_every_feed_defaults_to_the_vault():
    settings = config.load({})
    assert all(settings.reads_vault(feed) for feed in config.FEEDS)


def test_a_feed_set_to_desk_is_not_read_from_the_vault():
    settings = config.load({"MACRO_FEED_SOURCE_CATALYSTS": "desk",
                            "MACRO_FEED_SOURCE_TAIL_RISKS": " Vault "})
    assert not settings.reads_vault("catalysts")
    assert settings.reads_vault("tail_risks")
    assert settings.reads_vault("holdings")


def test_anything_but_vault_or_desk_refuses_to_start():
    with pytest.raises(config.Misconfigured, match="MACRO_FEED_SOURCE_HOLDINGS"):
        config.load({"MACRO_FEED_SOURCE_HOLDINGS": "openclaw"})


def test_a_note_the_desk_owns_is_not_opened_and_can_retire_nothing(tmp_path: Path):
    monitor = tmp_path / "Catalyst Monitor.md"
    monitor.write_text(CATALYSTS, encoding="utf-8")
    read = vault.read_all(
        catalyst_file=monitor, watchlist_file=None, blackswan_file=None,
        seen_on=date(2026, 9, 21),
    )
    assert [one.name for one in read.catalysts] == ["U.S. August CPI"]
    assert read.catalysts_whole
    assert read.holdings == [] and read.tail_risks == []
    assert not read.holdings_whole and not read.tail_risks_whole
    assert read.findings == [], "a note that is not read is not a missing file"

    skipped = vault.read_all(
        catalyst_file=None, watchlist_file=None, blackswan_file=None,
        seen_on=date(2026, 9, 21),
    )
    assert skipped.catalysts == [] and not skipped.catalysts_whole


@pytest.mark.parametrize(
    ("variable", "value", "said"),
    [
        ("MACRO_FEED_SOURCE_CATALYSTS", "obsidian", "MACRO_FEED_SOURCE_CATALYSTS is 'obsidian'"),
        ("DATABASE_URL", "postgresql://u@db.example:6543/postgres", "port 6543"),
    ],
)
def test_a_bad_setting_is_a_sentence_and_exit_2_not_a_traceback(
    monkeypatch, caplog, variable, value, said
):
    """The worker refuses to start in one sentence, whatever mode it was asked for."""
    import dotenv

    from macro import main

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.setenv(variable, value)
    for flag in ("--rescore", "--etf-backfill"):
        caplog.clear()
        assert main.run([flag, "--dry-run"]) == 2
        assert said in caplog.text
