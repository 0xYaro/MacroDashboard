"""The judgment run for the MacroDashboard regime read.

Two modes in one package.

`narrative` runs on each Singapore weekday after the 09:30 full run has
landed. It fills the series whose `fetch_path` is `agent_0930`, which the
worker deliberately cannot fill because it holds no LLM client, and it writes
`macro_core.event`.

`author` runs on Monday after the day's narrative (Tuesday when Monday produced
no proposal). It grades the prior
week, appends the ledgers, authors the new phase calls, scenario set, setups and
strategy as `proposed`, runs the R1 to R12 preflight, and stops.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
