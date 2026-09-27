"""Joining many per-item faults into one sentence that says each reason once.

A source that reads fourteen symbols from one host fails at all fourteen for
one reason. The rate-limit breaker in `http.py` is the clearest case: after two
consecutive 429s every further request to that host is refused with the same
explanation, so the run recorded fourteen copies of one sentence and the web app
rendered the pair of them, the `ingest_run` error and the finding built from it,
twenty-eight times.

The fix is in the joining rather than in the messages. A fault is written as
``"<subject>: <explanation>"`` by every fetcher in this worker, so faults that
share an explanation can be collected under it and the subjects listed once.
Every subject stays in the sentence: a grouped sentence that said "and 13
others" would hide which symbols were lost, which is the fact the operator needs.

A fault with no ``": "`` in it is kept whole as its own explanation. That is
what a source-level error looks like, and inventing a subject for it would put
the first clause of a sentence in front of its own colon.
"""

from __future__ import annotations

from collections.abc import Iterable

#: What separates one group from the next. Explanations carry their own
#: semicolons, so the groups read as clauses of one sentence either way; the
#: repetition, not the punctuation, was what made the old sentence unreadable.
JOIN = "; "


def split_fault(fault: str) -> tuple[str | None, str]:
    """One fault as the subject it happened to and the reason it happened.

    Split on the first ``": "`` because that is how every fetcher builds one.
    A subject is a symbol, a series id or a year, none of which carries a colon.
    """
    subject, sep, explanation = fault.partition(": ")
    if not sep:
        return None, fault
    return subject, explanation


def group_faults(faults: Iterable[str]) -> str:
    """Every fault in one sentence, with each distinct explanation said once.

    Explanations keep the order they first appeared in, so the symbol that
    actually tripped the breaker still leads the sentence and the ones skipped
    behind it follow.
    """
    grouped: dict[str, list[str | None]] = {}
    for fault in faults:
        if not fault:
            continue
        subject, explanation = split_fault(fault)
        grouped.setdefault(explanation, []).append(subject)
    return JOIN.join(
        _one(explanation, subjects) for explanation, subjects in grouped.items()
    )


def _one(explanation: str, subjects: list[str | None]) -> str:
    named = [subject for subject in subjects if subject]
    if not named:
        return explanation
    if len(named) == 1:
        return f"{named[0]}: {explanation}"
    # The count is stated as well as the list, so an operator reading a long
    # sentence knows how many were lost without counting commas.
    return f"{', '.join(named)} ({len(named)} in all): {explanation}"
