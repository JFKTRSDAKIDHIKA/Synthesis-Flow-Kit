#!/usr/bin/env python3
"""Write a copy of a Liberty with the real cell areas taken from its LEF.

The USC-3N-2D standard-cell Liberty declares `area : 0;` for all 57 cells, so
neither Yosys' `stat -liberty` nor ABC's area cost model sees anything. The LEF
that accompanies the same library does carry the physical footprint
(`SIZE w BY h`), so the areas are taken from there.

Nothing else in the Liberty is touched and the PDK is never written to: the
result goes to --out, inside the build tree.

    lib_add_area.py --lef <stdcell.lef> --liberty <stdcell.lib> --out <patched.lib>
"""

from __future__ import annotations

import argparse
import json
import re
import sys

MACRO_RE = re.compile(r"^\s*MACRO\s+(\S+)")
SIZE_RE = re.compile(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)")
CELL_RE = re.compile(r"^\s*cell\s*\(\s*\"?([^)\"]+?)\"?\s*\)")
AREA_RE = re.compile(r"^(\s*)area\s*:\s*([\d.]+)\s*;")


def lef_sizes(path: str) -> dict[str, float]:
    sizes: dict[str, float] = {}
    macro = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = MACRO_RE.match(line)
            if m:
                macro = m.group(1)
                continue
            m = SIZE_RE.match(line)
            if m and macro:
                sizes[macro] = float(m.group(1)) * float(m.group(2))
    return sizes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lef", required=True)
    ap.add_argument("--liberty", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", help="where to write a JSON record of the patch")
    args = ap.parse_args()

    sizes = lef_sizes(args.lef)
    if not sizes:
        raise SystemExit("ERROR: no `MACRO ... SIZE w BY h` in %s" % args.lef)

    cell = None
    patched, already, missing = 0, 0, []
    with open(args.liberty, encoding="utf-8", errors="replace") as src, \
         open(args.out, "w", encoding="utf-8") as dst:
        for line in src:
            m = CELL_RE.match(line)
            if m:
                cell = m.group(1).strip()
                if cell not in sizes:
                    missing.append(cell)
            else:
                m = AREA_RE.match(line)
                if m and cell and cell in sizes:
                    if float(m.group(2)) == 0.0:
                        line = "%sarea : %.6f;\n" % (m.group(1), sizes[cell])
                        patched += 1
                    else:
                        already += 1
            dst.write(line)

    report = {
        "lef": args.lef, "liberty": args.liberty, "out": args.out,
        "cells_in_lef": len(sizes), "areas_patched": patched,
        "areas_already_set": already, "cells_missing_from_lef": sorted(missing),
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
            fh.write("\n")
    print("            %d cells in the LEF, %d `area : 0;` replaced, %d already set"
          % (len(sizes), patched, already))
    if missing:
        print("            not in the LEF, left alone: %s" % ", ".join(sorted(missing)))
    if patched == 0 and already == 0:
        raise SystemExit("ERROR: no `area :` line matched a cell; is %s a Liberty?"
                         % args.liberty)
    return 0


if __name__ == "__main__":
    sys.exit(main())
