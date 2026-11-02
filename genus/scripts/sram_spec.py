#!/usr/bin/env python3
"""Extract the SRAM macros a configuration needs from its SystemVerilog.

Two kinds of on-chip memory become FakeRAM macros:

  * instances of the `Sram` wrapper in hdl/verilog/Sram.v, instantiated as

        Sram #(
          .GLOBAL_BASE_ADDR(1048576),
          .NUM_ENTRIES(2048)
        ) sramModules_0 ( ... );

    whose USE_FAKERAM branch instantiates `fakeram_<depth>x128` itself. The
    depth is the NUM_ENTRIES value and the width that of the wrapper's `wdata`.
    The 128-bit TCMs of the RVV/VME configurations take this path.

  * memories firtool emitted from a Chisel SyncReadMem, as one module
    `<name>_<depth>x<width>` with a single read-write port (RW0_*) or with one
    read and one write port (R0_*, W0_*). The RTL has no macro branch for
    these, so flow/scripts/map_sram_rtl.py swaps each such module body for a
    wrapper around FakeRAM macros before synthesis. The 512-bit TCMs and the
    matrix register file of AMEOOO take this path.

A firtool memory is mapped only when its read is registered (a SyncReadMem,
not an asynchronous Mem) and it has at least --min-depth rows; shallower ones
are left to synthesis as flip-flops, which is what a register file that small
is. A memory wider than --max-width is split into --max-width slices side by
side, so a 512-bit TCM becomes four 128-bit macros, the shape the wrapper
path already uses. Memories with any other port set are left as logic and
listed, with their size, under `unmapped`.

    sram_spec.py --rtl build/<config>/rtl/<top>.sv --top <top> --out spec.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys

_INST_RE = re.compile(
    r"""(?<![\w.])Sram\s*\#\(                       # the wrapper, instantiated
        (?P<params>(?:\s*\.\w+\s*\([^()]*\)\s*,?)+) # .NAME(value), ...
        \s*\)\s*(?P<inst>\w+)\s*\(""",
    re.X,
)

_NUM_ENTRIES_RE = re.compile(r"\.NUM_ENTRIES\s*\(\s*(\d+)\s*\)")

# One port-list line. firtool groups consecutive ports of the same direction
# and type, writing the direction and range on the first only; the ANSI rule
# is that the others inherit both.
_PORT_LINE_RE = re.compile(
    r"^\s*(?:(input|output)\b)?\s*(?:\[\s*([^\]:]+?)\s*:\s*([^\]]+?)\s*\])?\s*(\w+)\s*[,)]?\s*$"
)

# `module bankData_memory_512x64(`: firtool names every memory it emits after
# the Chisel val and the array shape.
_MEM_MODULE_RE = re.compile(r"^module\s+(\w+?_(\d+)x(\d+))\s*\(", re.M)

_ONE_RW = {"RW0_addr", "RW0_en", "RW0_clk", "RW0_wmode", "RW0_wdata", "RW0_rdata"}
_ONE_R_ONE_W = {"R0_addr", "R0_en", "R0_clk", "R0_data",
                "W0_addr", "W0_en", "W0_clk", "W0_data"}


def port_widths(text: str, start: int) -> dict[str, int | None]:
    """Widths of the ports of the module whose header begins at `start`.

    A range that is not two integer literals (`[$clog2(N)-1:0]`) has no width
    here; None stands for it.
    """
    header = text[text.index("(", start) + 1 : text.index(");", start)]
    widths: dict[str, int | None] = {}
    width: int | None = 1
    for line in header.splitlines():
        line = line.split("//")[0]
        if not line.strip():
            continue
        m = _PORT_LINE_RE.match(line)
        if m is None:
            raise SystemExit("ERROR: cannot parse port line %r at offset %d"
                             % (line, start))
        direction, msb, lsb, name = m.groups()
        if direction is not None:
            if msb is None:
                width = 1
            elif msb.isdigit() and lsb.isdigit():
                width = int(msb) - int(lsb) + 1
            else:
                width = None
        widths[name] = width
    return widths


def wrapper_spec(text: str) -> dict | None:
    """Depth histogram and port widths of the `Sram` wrapper, if the RTL has it."""
    start = re.search(r"^module\s+Sram\s*#\(", text, re.M)
    if start is None:
        return None
    head = text[start.start() : start.start() + 4000]
    params_end = start.start() + head.index(")")
    widths = port_widths(text, params_end)
    for pin in ("wdata", "wmask", "rdata"):
        if pin not in widths:
            raise SystemExit("ERROR: the Sram wrapper has no `%s` port" % pin)
    if widths["rdata"] != widths["wdata"]:
        raise SystemExit("ERROR: the Sram wrapper's rdata and wdata differ in width")

    counts: dict[int, int] = {}
    for m in _INST_RE.finditer(text):
        entries = _NUM_ENTRIES_RE.search(m.group("params"))
        if entries is None:
            # The wrapper's own default; the design never relies on it, so an
            # instance without the parameter is a change worth stopping on.
            raise SystemExit(
                "ERROR: `Sram` instance %s has no NUM_ENTRIES parameter"
                % m.group("inst")
            )
        depth = int(entries.group(1))
        counts[depth] = counts.get(depth, 0) + 1
    return {"data_bits": widths["wdata"], "mask_bits": widths["wmask"],
            "depths": counts}


def instances_of(text: str, module: str) -> list[str]:
    """The port-connection text of every instance of `module`."""
    return [m.group(1) for m in re.finditer(
        r"^\s+%s\s+\w+\s*\((.*?)\n\s*\);" % re.escape(module), text, re.M | re.S)]


def connection(inst: str, port: str) -> str:
    m = re.search(r"\.%s\s*\(\s*(.*?)\s*\)\s*(?:,|$)" % port, inst, re.S)
    return m.group(1) if m else ""


def firtool_memories(text: str, min_depth: int, max_width: int):
    """(memories to map, memories left as logic) among firtool's emitted ones."""
    mapped, unmapped = [], []
    for m in _MEM_MODULE_RE.finditer(text):
        module, depth, width = m.group(1), int(m.group(2)), int(m.group(3))
        body = text[m.start() : text.index("\nendmodule", m.start())]
        widths = port_widths(text, m.start())
        names = set(widths)
        insts = instances_of(text, module)
        entry = {"module": module, "depth": depth, "width": width,
                 "instances": len(insts), "bits": depth * width * len(insts)}

        if _ONE_RW <= names and names <= _ONE_RW | {"RW0_wmask"}:
            ports, mask_port, sync_reg = "1rw", "RW0_wmask", "_RW0_raddr_d0"
        elif _ONE_R_ONE_W <= names and names <= _ONE_R_ONE_W | {"W0_mask"}:
            ports, mask_port, sync_reg = "1r1w", "W0_mask", "_R0_addr_d0"
        else:
            # Only a few firtool-named modules are memories at all; anything
            # with another port set is not ours to judge by name alone.
            if names & (_ONE_RW | _ONE_R_ONE_W):
                unmapped.append(dict(entry, reason="port set %s is not 1RW or 1R1W"
                                     % ",".join(sorted(names))))
            continue

        if not re.search(r"\b%s\b" % sync_reg, body):
            # An asynchronous-read Mem (queues, small tables): no SRAM macro
            # reads combinationally, so these are logic by construction.
            continue
        if depth < min_depth:
            unmapped.append(dict(entry, reason="depth %d < SRAM_MIN_DEPTH %d"
                                 % (depth, min_depth)))
            continue
        if widths["RW0_wdata" if ports == "1rw" else "W0_data"] != width:
            raise SystemExit("ERROR: %s's data port is not %d bits wide" % (module, width))

        mask_bits = widths.get(mask_port, 0)
        if mask_bits and width % mask_bits:
            raise SystemExit("ERROR: %s has a %d-bit mask over %d data bits"
                             % (module, mask_bits, width))
        slice_width = min(width, max_width)
        if width % slice_width:
            unmapped.append(dict(entry, reason="width %d is not a multiple of "
                                 "SRAM_MAX_WIDTH %d" % (width, max_width)))
            continue
        if ports == "1r1w":
            # One macro clock: the flow maps only memories whose two ports
            # share it at every instance.
            for inst in insts:
                if connection(inst, "R0_clk") != connection(inst, "W0_clk"):
                    raise SystemExit("ERROR: an instance of %s clocks its read and "
                                     "write ports differently; a 1R1W macro has "
                                     "one clock" % module)
        mapped.append(dict(entry, ports=ports, mask_bits=mask_bits,
                           slice_width=slice_width, slices=width // slice_width))
    return mapped, unmapped


def macro_name(prefix: str, ports: str, depth: int, width: int) -> str:
    if ports == "1rw":
        return "%s_%dx%d" % (prefix, depth, width)
    return "%s_%s_%dx%d" % (prefix, ports, depth, width)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtl", required=True, help="emitted SystemVerilog")
    ap.add_argument("--top", required=True, help="top-level module name")
    ap.add_argument("--prefix", default="fakeram",
                    help="macro name prefix; must match hdl/verilog/Sram.v "
                         "(default: %(default)s)")
    ap.add_argument("--min-depth", type=int, default=64,
                    help="shallowest firtool memory mapped to a macro "
                         "(default: %(default)s)")
    ap.add_argument("--max-width", type=int, default=128,
                    help="widest macro; wider memories are sliced "
                         "(default: %(default)s)")
    ap.add_argument("--out", required=True, help="where to write the spec JSON")
    args = ap.parse_args()

    text = open(args.rtl, encoding="utf-8", errors="replace").read()
    wrapper = wrapper_spec(text)
    mapped, unmapped = firtool_memories(text, args.min_depth, args.max_width)

    # One entry per macro, however many memories need it.
    macros: dict[str, dict] = {}

    def need(ports: str, depth: int, width: int, count: int, from_wrapper: bool):
        name = macro_name(args.prefix, ports, depth, width)
        m = macros.setdefault(name, {
            "name": name, "ports": ports, "depth": depth, "width": width,
            "addr_bits": max(1, math.ceil(math.log2(depth))),
            # FakeRAM's own area model splits the array over banks; keeping
            # the bank count proportional to depth stops the deep macros
            # coming out as extreme slivers. It does not change the area.
            "banks": 4 if depth >= 2048 else (2 if depth >= 512 else 1),
            "instances": 0, "from_wrapper": False,
        })
        m["instances"] += count
        m["from_wrapper"] = m["from_wrapper"] or from_wrapper
        return name

    if wrapper is not None:
        for depth, count in sorted(wrapper["depths"].items()):
            need("1rw", depth, wrapper["data_bits"], count, True)
    for mem in mapped:
        mem["macro"] = need(mem["ports"], mem["depth"], mem["slice_width"],
                            mem["instances"] * mem["slices"], False)

    if not macros:
        raise SystemExit(
            "ERROR: no memory in %s maps to a macro: no `Sram` wrapper instance "
            "and no firtool SyncReadMem of at least %d rows. A configuration "
            "with no on-chip memory cannot use this flow's SRAM stage."
            % (args.rtl, args.min_depth)
        )

    for m in macros.values():
        m["bits"] = m["depth"] * m["width"]
    spec = {
        "top": args.top,
        "rtl": args.rtl,
        "min_depth": args.min_depth,
        "max_width": args.max_width,
        "wrapper": wrapper and {"data_bits": wrapper["data_bits"],
                                "mask_bits": wrapper["mask_bits"]},
        "memories": mapped,
        "unmapped": unmapped,
        "macros": sorted(macros.values(), key=lambda m: (m["ports"], m["depth"],
                                                          m["width"])),
        "total_bits": sum(m["bits"] * m["instances"] for m in macros.values()),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)
        fh.write("\n")

    for mem in mapped:
        print("  %-26s %-4s %5dx%-4d x%-3d -> %d x %s"
              % (mem["module"], mem["ports"], mem["depth"], mem["width"],
                 mem["instances"], mem["slices"], mem["macro"]))
    for m in spec["macros"]:
        print("  %-26s %-4s %5dx%-4d x%d instance(s)"
              % (m["name"], m["ports"], m["depth"], m["width"], m["instances"]))
    for mem in unmapped:
        print("  left as logic: %-24s %5dx%-4d x%-3d (%d bits): %s"
              % (mem["module"], mem["depth"], mem["width"], mem["instances"],
                 mem["bits"], mem["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
