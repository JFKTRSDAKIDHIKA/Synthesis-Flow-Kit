#!/usr/bin/env python3
"""Stop before synthesis if the design asks for an SRAM depth Sram.v cannot map.

The USE_FAKERAM branch of hdl/verilog/Sram.v instantiates one named macro per
supported depth, because SystemVerilog cannot build a module name out of a
parameter. So the set of depths it supports is finite and written down in it:

    localparam int FAKERAM_DEPTHS [3] = '{128, 512, 2048};

Without this check an unsupported depth still fails, but late and obscurely:
sram_spec.py would happily read the depth out of the RTL and FakeRAM would
build a macro for it, and only then would the frontend stop on the `$error` in
the wrapper's else-branch -- after a model that nothing instantiates had been
generated. This compares the two sets directly and says which depth is missing
and where to add it.

Two sources are read, and they have to agree:

  * the FAKERAM_DEPTHS declaration, which is what a person reads, and
  * the `fakeram_<depth>x<width>` instances in the branches below it, which is
    what the tools actually see,

so a stale list is itself an error rather than a silent disagreement.

    check_sram_support.py --rtl <top.sv> --spec <sram.spec.json>

--rtl is the SystemVerilog about to be synthesized, which carries its own copy
of the wrapper; that is the copy that matters, including when the flow is run
with RTL_SV pointing at RTL it did not generate.

Only macros the wrapper instantiates are checked. Macros for firtool-emitted
memories are instantiated by the wrappers map_sram_rtl.py writes, for whatever
shape the spec asks for, so there is no list for them to disagree with; a
configuration with no `Sram` instance at all has nothing to check here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# `localparam int FAKERAM_DEPTHS [3] = '{128, 512, 2048};`, tolerant about the
# type, the packed/unpacked dimension and the spacing.
DEPTHS_RE = re.compile(
    r"localparam\s+[^;]*?\bFAKERAM_DEPTHS\b[^=]*=\s*'\{([^}]*)\}\s*;", re.S
)

# The branch guard of the USE_FAKERAM section, so the region searched for macro
# instances is that section and not the whole file.
SECTION_RE = re.compile(r"^`elsif\s+USE_FAKERAM\b(.*?)^`(?:elsif|else|endif)\b",
                        re.M | re.S)

MACRO_RE = re.compile(r"^\s*fakeram_(\d+)x(\d+)\s+\w+\s*\(", re.M)


def supported_depths(rtl: str, path: str) -> tuple[set[int], set[int]]:
    """(depths the list declares, depths the branches instantiate)."""
    section = SECTION_RE.search(rtl)
    if section is None:
        raise SystemExit(
            "ERROR: %s has no `elsif USE_FAKERAM section.\n"
            "       The flow synthesizes with -DUSE_FAKERAM, so the SRAM\n"
            "       wrapper in this RTL predates the FakeRAM branch. Regenerate\n"
            "       it (`make rtl`), or drop USE_FAKERAM from SYNTH_DEFINES to\n"
            "       synthesize the memories as flip-flops." % path
        )
    body = section.group(1)

    declared = DEPTHS_RE.search(body)
    if declared is None:
        raise SystemExit(
            "ERROR: %s declares no FAKERAM_DEPTHS in its USE_FAKERAM branch.\n"
            "       hdl/verilog/Sram.v is meant to list the supported depths\n"
            "       there; flow/scripts/check_sram_support.py reads it." % path
        )
    listed = {int(v) for v in re.findall(r"\d+", declared.group(1))}
    instantiated = {int(d) for d, _w in MACRO_RE.findall(body)}
    return listed, instantiated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtl", required=True, help="the SystemVerilog to be synthesized")
    ap.add_argument("--spec", required=True, help="sram_spec.py output")
    ap.add_argument("--source", default="hdl/verilog/Sram.v",
                    help="where to tell the user to add a branch")
    args = ap.parse_args()

    spec = json.load(open(args.spec, encoding="utf-8"))
    needed = {m["depth"]: m for m in spec["macros"] if m.get("from_wrapper", True)}
    if not needed:
        print("  no `Sram` wrapper instances; %d macro(s) come from firtool "
              "memories mapped by map_sram_rtl.py" % len(spec["macros"]))
        return 0

    rtl = open(args.rtl, encoding="utf-8", errors="replace").read()
    listed, instantiated = supported_depths(rtl, args.rtl)

    if listed != instantiated:
        only_listed = sorted(listed - instantiated)
        only_built = sorted(instantiated - listed)
        lines = ["ERROR: the FAKERAM_DEPTHS list and the branches below it disagree",
                 "       in %s." % args.rtl]
        if only_listed:
            lines.append("       listed but no branch instantiates it: %s"
                         % ", ".join(str(d) for d in only_listed))
        if only_built:
            lines.append("       instantiated but not listed: %s"
                         % ", ".join(str(d) for d in only_built))
        lines.append("       Fix %s so the two match." % args.source)
        raise SystemExit("\n".join(lines))

    missing = sorted(set(needed) - listed)
    if missing:
        raise SystemExit(
            "ERROR: %s needs SRAM depths that %s cannot map: %s\n"
            "       supported: %s\n"
            "       Add each missing depth to FAKERAM_DEPTHS and give it a\n"
            "       `fakeram_<depth>x%d` branch in the USE_FAKERAM section of\n"
            "       %s, then regenerate the RTL."
            % (spec["top"], args.source,
               ", ".join(str(d) for d in missing),
               ", ".join(str(d) for d in sorted(listed)),
               spec["wrapper"]["data_bits"], args.source)
        )

    print("  supported depths %s; %s needs %s"
          % (", ".join(str(d) for d in sorted(listed)), spec["top"],
             ", ".join(str(d) for d in sorted(needed)) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
