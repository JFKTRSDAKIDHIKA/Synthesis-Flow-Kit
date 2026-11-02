#!/usr/bin/env python3
"""Roll one run up into summary.json and summary.txt.

A run is one (CONFIG, PDK, library flavour, corner, ABC target, fanout repair)
point, which is exactly one directory under build/. Everything here is read
back out of that directory: the area from Yosys' `stat -liberty` or exact hierarchical Liberty
cell counting on the netlist STA saw, the macro areas from the SRAM stage, and the timing from the OpenSTA
results. Nothing is recomputed and nothing is estimated.

The area split is taken from the precise numbers rather than from the histogram:
Yosys prints the per-cell-type totals in %g form, so a macro shows up as
`1.9E+03`, while the chip total and the sequential total are printed in full.
Macro area therefore comes from the SRAM stage, multiplied by the instance
count the netlist actually has, and combinational area is what is left.

A block-synthesized netlist is hierarchical, and `stat` then reports every
module on its own before a `design hierarchy` section with the totals of the
whole design; the totals, the histogram and the per-block areas are read from
that section.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# `   Chip area for module '\AmeB8OOO': 1234567.890000`
CHIP_AREA_RE = re.compile(r"Chip area for module '\\?(\S+?)':\s*([\d.]+)")
# `     of which used for sequential elements: 41.180832 (2.10%)`
SEQ_AREA_RE = re.compile(r"used for sequential elements:\s*([\d.]+)")
# A `stat` histogram line: count, area, cell name. Yosys prints `-` in the area
# column for the wire and port lines that share the same shape, so the area has
# to start with a digit for the line to be a cell.
CELL_RE = re.compile(r"^\s+(\d+)\s+(\d[\d.eE+-]*)\s+(\S+)\s*$", re.M)


# `   Chip area for top module '\AMEOOO': 1234567.890000`
TOP_AREA_RE = re.compile(r"Chip area for top module '\\?(\S+?)':\s*([\d.]+)")


def read_area(path: str) -> dict:
    """Chip/sequential area and histogram from an exact Liberty count report."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as stream:
        text = stream.read()
    modules = {}
    if "=== design hierarchy ===" in text:
        per_module, _, text = text.partition("=== design hierarchy ===")
        # each module's own area, its submodules excluded
        for m in CHIP_AREA_RE.finditer(per_module):
            modules[m.group(1)] = float(m.group(2))
        # the design totals: the last table, up to its `submodules` lines
        rest = text.rpartition("Count including submodules.")[2]
        text = rest.split(" submodules\n")[0] + "\n" + rest[rest.find("Chip area"):]
        chip = TOP_AREA_RE.search(text)
    else:
        chip = CHIP_AREA_RE.search(text)
    seq = SEQ_AREA_RE.search(text)
    # `stat` prints its own total as a line of the same shape, named `cells`.
    histogram = {name: {"count": int(n), "area_um2": float(a)}
                 for n, a, name in CELL_RE.findall(text) if name != "cells"}
    return {
        "chip_area_um2": float(chip.group(2)) if chip else None,
        "sequential_um2": float(seq.group(1)) if seq else None,
        "cells": histogram,
        "cell_count": sum(c["count"] for c in histogram.values()),
        "modules": modules,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, help="build/<config>/<variant>")
    ap.add_argument("--sram-json", required=True)
    ap.add_argument("--blocks-json", default=None,
                    help="assemble_netlist.py's blocks.json, for a block synthesis")
    ap.add_argument("--std-lib", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--chisel-config", required=True)
    ap.add_argument("--top", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--params", required=True,
                    help="KEY=VALUE knobs to record with the result")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-txt", required=True)
    args = ap.parse_args()

    run = args.run_dir
    sram = json.load(open(args.sram_json, encoding="utf-8"))
    macro_area = {m["name"]: m["area_um2"] for m in sram["macros"]}

    area = read_area(os.path.join(run, "synth", "reports", "area.txt"))
    histogram = area.get("cells", {})

    macros = []
    n_macro, a_macro = 0, 0.0
    for m in sram["macros"]:
        count = histogram.get(m["name"], {}).get("count", 0)
        n_macro += count
        a_macro += count * macro_area[m["name"]]
        macros.append(dict(m, instances_in_netlist=count,
                           total_area_um2=round(count * macro_area[m["name"]], 3)))

    total = area.get("chip_area_um2")
    a_seq = area.get("sequential_um2")
    a_comb = (round(total - a_macro - a_seq, 3)
              if None not in (total, a_seq) else None)
    # Splitting the *cell count* into sequential and combinational would need
    # the Liberty; the area split does not, so only the total and the macro
    # count are reported. The full histogram is in summary.json either way.
    n_cells = area.get("cell_count")

    blocks = []
    if args.blocks_json and os.path.isfile(args.blocks_json):
        bj = json.load(open(args.blocks_json, encoding="utf-8"))
        own = area.get("modules", {})
        children = {u["unit"]: u.get("children", {}) for u in bj["units"]}
        memo = {}

        def with_nested(name):
            """A module's area with its nested blocks, one instance."""
            if name not in memo:
                a = own.get(name)
                for c, n in children.get(name, {}).items():
                    sub = with_nested(c)
                    a = None if a is None or sub is None else a + n * sub
                memo[name] = a
            return memo[name]

        for u in bj["units"]:
            if not u["block"]:
                continue
            a = with_nested(u["unit"])
            blocks.append({
                "unit": u["unit"], "block": u["block"], "ties": u["ties"],
                "instances": u["instances_in_design"],
                "area_each_um2": a,
                "area_total_um2": round(a * u["instances_in_design"], 3) if a is not None else None,
                "comb_inputs": len(u["comb_paths"]),
            })

    timing = {}
    rdir = os.path.join(run, "sta", "reports")
    if os.path.isdir(rdir):
        for name in sorted(os.listdir(rdir)):
            if name.startswith("tmin-") and name.endswith(".json"):
                timing[name[len("tmin-"):-len(".json")]] = \
                    json.load(open(os.path.join(rdir, name), encoding="utf-8"))

    params = {}
    for kv in args.params.split():
        key, _, value = kv.partition("=")
        params[key] = value

    result = {
        "config": args.config,
        "chisel_config": args.chisel_config,
        "top": args.top,
        "variant": args.variant,
        "params": params,
        "std_lib": args.std_lib,
        "area": {
            "total_um2": total,
            "macro_um2": round(a_macro, 3),
            "sequential_um2": a_seq,
            "combinational_um2": a_comb,
            "cells": {"total": n_cells, "macro": n_macro},
            "histogram": histogram,
        },
        "sram": {"macros": macros,
                 "total_macro_area_um2": sram["total_macro_area_um2"]},
        "blocks": blocks,
        "timing": timing,
    }
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
        fh.write("\n")

    lines = []
    w = lines.append
    w("CoralNPU AME synthesis summary")
    w("=" * 62)
    w("%-22s %s" % ("config", "%s (%s)" % (args.config, args.chisel_config)))
    w("%-22s %s" % ("top", args.top))
    w("%-22s %s" % ("variant", args.variant))
    for key in sorted(params):
        w("%-22s %s" % (key, params[key]))
    w("")
    w("Area (um^2, Yosys stat -liberty on the netlist STA saw)")
    w("-" * 62)
    if total is None:
        w("  no area report; run `make area`")
    else:
        w("  %-20s %14.1f  (%s cells)" % ("total", total, n_cells))
        w("  %-20s %14.1f  (%d macros)" % ("SRAM macros", a_macro, n_macro))
        if a_seq is not None:
            w("  %-20s %14.1f" % ("sequential", a_seq))
            w("  %-20s %14.1f" % ("combinational", a_comb))
    w("")
    w("SRAM macros (FakeRAM2.0)")
    w("-" * 62)
    for m in macros:
        w("  %-22s %-4s %5dx%-4d x%-3d %10.1f um^2 each"
          % (m["name"], m.get("ports", "1rw"), m["depth"], m["width"],
             m["instances_in_netlist"], macro_area[m["name"]]))
    if any(m.get("ports", "1rw") != "1rw" for m in macros):
        w("  1R1W area = 1RW area of the same shape x %s (assumed; FakeRAM2.0 "
          "has no two-port model)" % sram.get("area_factor_2p", 1.0))
    if blocks:
        w("")
        w("Blocks (each synthesized once; area includes nested blocks)")
        w("-" * 62)
        for b in blocks:
            w("  %-34s x%-4d %10s um^2 each %12s total"
              % (b["unit"], b["instances"],
                 "%.1f" % b["area_each_um2"] if b["area_each_um2"] is not None else "?",
                 "%.1f" % b["area_total_um2"] if b["area_total_um2"] is not None else "?"))
    w("")
    w("Timing (OpenSTA)")
    w("-" * 62)
    if not timing:
        w("  no Tmin result; run `make tmin`")
    for mode, t in sorted(timing.items()):
        w("  %-20s Tmin %s ps  (violates at %s ps)"
          % (mode, t.get("lowest_met_ps"), t.get("highest_violated_ps")))
        if t.get("t_critical_ps") is not None:
            w("  %-20s critical path %.2f ps" % ("", t["t_critical_ps"]))
        w("  %-20s Fmax %.1f MHz" % ("", t["fmax_mhz"]))
        w("  %-20s worst hold %+.3f ps" % ("", t["worst_hold_ps"]))
    text = "\n".join(lines) + "\n"
    with open(args.out_txt, "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
