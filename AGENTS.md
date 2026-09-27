# Agent notes for this repository

Lessons written by agents after being corrected here. Add to it; do not rewrite it.

## The system map

`docs/system-map.html` is the operator's map of the whole system: what runs, when, where AI is involved, and what each setting touches.
Whenever you change the system (worker, runner, agent, desk, OpenClaw jobs, migrations, web), add an entry to the `CHANGES` list at the bottom of that file, with the `areas` it touches so the map highlights them, and update any map text the change makes wrong.
Then open it for the operator with `lavish-axi docs/system-map.html` and point them to it in your reply, naming the areas being fixed.
