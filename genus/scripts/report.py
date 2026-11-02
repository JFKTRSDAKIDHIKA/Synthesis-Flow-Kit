#!/usr/bin/env python3
"""Collect every run under build/ into one table.

    report.py --build-dir build --out-json build/report.json --out-txt build/report.txt

One row per (config, variant). A sweep over ABC targets therefore shows every
target and, for each configuration, which one reached the lowest Tmin: the
reported Fmax of a configuration is a maximum over the sweep rather than one
target's outcome.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def find_summaries(build_dir: str):
    for root, dirs, files in os.walk(build_dir):
        dirs[:] = [d for d in dirs if d not in (".staging", "fakeram")]
        if os.path.basename(root) == "summary" and "summary.json" in files:
            yield os.path.join(root, "summary.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-txt", required=True)
    args = ap.parse_args()

    rows = []
    for path in sorted(find_summaries(args.build_dir)):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("  skipping %s: %s" % (path, exc), file=sys.stderr)
            continue
        timing = d.get("timing", {}).get("reg2reg") or \
            next(iter(d.get("timing", {}).values()), {})
        rows.append({
            "config": d.get("config"),
            "variant": d.get("variant"),
            "top": d.get("top"),
            "abc_ps": d.get("params", {}).get("abc_ps"),
            "area_um2": d.get("area", {}).get("total_um2"),
            "macro_um2": d.get("area", {}).get("macro_um2"),
            "sequential_um2": d.get("area", {}).get("sequential_um2"),
            "cells": d.get("area", {}).get("cells", {}).get("total"),
            "tmin_ps": timing.get("lowest_met_ps"),
            "t_critical_ps": timing.get("t_critical_ps"),
            "fmax_mhz": timing.get("fmax_mhz"),
            "summary": path,
        })

    # The best run of a configuration is the one that reached the highest Fmax,
    # so a sweep's reported Fmax is a maximum over the sweep rather than one
    # ABC target's outcome.
    best: dict[str, dict] = {}
    for r in rows:
        if r["fmax_mhz"] is None:
            continue
        cur = best.get(r["config"])
        if cur is None or r["fmax_mhz"] > cur["fmax_mhz"]:
            best[r["config"]] = r
    for r in rows:
        r["best_for_config"] = best.get(r["config"]) is r

    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump({"runs": rows}, fh, indent=1)
        fh.write("\n")

    header = "%-22s %-40s %8s %12s %12s %10s %10s %s" % (
        "config", "variant", "ABC ps", "area um2", "macro um2", "Tcrit ps",
        "Fmax MHz", "best")
    lines = [header, "-" * len(header)]
    fmt = lambda v, f: (f % v) if isinstance(v, (int, float)) else "-"
    for r in rows:
        lines.append("%-22s %-40s %8s %12s %12s %10s %10s %s" % (
            r["config"] or "-", r["variant"] or "-", r["abc_ps"] or "-",
            fmt(r["area_um2"], "%.1f"), fmt(r["macro_um2"], "%.1f"),
            fmt(r["t_critical_ps"], "%.2f"), fmt(r["fmax_mhz"], "%.1f"),
            "*" if r["best_for_config"] else ""))
    if not rows:
        lines.append("(no runs under %s; run `make all` first)" % args.build_dir)
    text = "\n".join(lines) + "\n"
    with open(args.out_txt, "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
