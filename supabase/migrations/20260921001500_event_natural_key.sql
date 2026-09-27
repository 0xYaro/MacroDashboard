-- macro_core.event had no natural key, which made it the one table where a replayed run
-- was not idempotent: a second 09:30 pass on the same day appended the whole log again.
-- Every other write in this system is an idempotent upsert, and an event log that grows on
-- replay is worse than most duplicates, because the dated bullets are what a later week's
-- grading reads back as what happened.
--
-- The key is the text's digest rather than the text, because an event line is long and a
-- btree index over it would be near the page limit.
create unique index if not exists uq_macro_event_natural
  on macro_core.event (occurred_on, kind, md5(text));
