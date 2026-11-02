# Synthesis Flow Kit

[The Genus reference methodology](genus/README.md) owns all new Cadence Genus
synthesis runs. Its single public entry is `genus/run.py`; configuration,
preparation, mapping, validation, Tcl and tests live together under `genus/`.
Start with that guide rather than copying scripts from an experiment directory.

The existing root Makefile and `scripts/` retain the older Design Compiler,
Yosys and STA examples. They are separate backends, not alternative Genus
entry points. Technology libraries, qualified design inputs and generated
results are external to the methodology source directory.

Raw synthesis results live under `runs/<unique-campaign-id>/` in this repository
and are Git-ignored. Publish compact validation records, not native databases,
netlists, source snapshots or logs.
