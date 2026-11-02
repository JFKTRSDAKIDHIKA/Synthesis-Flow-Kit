# Genus synthesis ownership

Use `genus/run.py` for every new Genus synthesis campaign. Read `genus/README.md`.
Keep reusable Genus Python, Tcl, preparation, profiles and tests inside `genus/`.
Do not create another Genus launcher in PnR-Flow-Kit, a workspace utility, or a
consumer checkout. Higher-level queues may call this entry point.

Each campaign keeps its configuration, source snapshot, input hashes, native
logs and outputs. Never bypass cache identity or overwrite a frozen attempt.
Library/RTL changes require a separately identified configuration. Preparation
and execution success do not establish timing closure or physical signoff.

Already active or queued frozen campaigns retain their original snapshots and
controllers until they finish; migration must not alter their hashes or restart
them. Preserve historical scripts as evidence, not as sources for new flows.
