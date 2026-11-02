#!/usr/bin/env python3
"""Put the per-unit netlists of a block synthesis back together, and check it.

    assemble_netlist.py --plan plan.json --units-dir <synth>/units --top AMEOOO \\
        --std-lib <lib> --sram-lib-dir <dir> --spec sram.spec.json \\
        --out <synth>/netlist/AMEOOO.v --blocks-json <synth>/blocks/blocks.json \\
        --report <synth>/blocks/blocks.txt

The netlist written is the unit netlists concatenated, top first: a
hierarchical gate-level netlist in which every block is a module of standard
cells and macros, instantiated by its parents by name. OpenSTA links it as it
is, so the top-level timing goes through every block's own cells; nothing is a
timing black box.

Before writing, this checks what a concatenation cannot check by itself:

  * every module is defined once, and every instantiated type is a Liberty
    cell, a macro or a module of the assembly;
  * every pin a parent connects on a block instance is a port of the block's
    netlist, and every input of the block is connected;
  * every macro the SRAM spec expects is in the hierarchy exactly as many
    times as the spec says (a block synthesized with a macro missing, or a
    black box left unlinked, fails here rather than as a smaller area);
  * the clock ports of every block instance are driven from the parent's
    clock network: its clock ports (the top's --clock-port) and whatever
    combinational logic they feed, which is where a clock gate (latch + AND)
    sits. A clock taken from a register output (a divided clock) or from
    logic the clock never reaches is an error; a gated one is reported.

It also reports, per block, the ports that reach clock pins and asynchronous
set/reset pins inside it, and the input ports with a purely combinational path
to an output port. Such a path is timed end to end by the top-level STA, but
no single ABC run saw it whole, so it is where the block boundary costs timing.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict, deque

from netlist_lib import expr_bits, elements, parse_modules, read_liberty


def load_units(plan, units_dir):
    """(unit, path, lines, modules) of every unit netlist, the top first."""
    out = []
    for u in plan["units"]:
        path = os.path.join(units_dir, u["unit"], "netlist", u["unit"] + ".v")
        if not os.path.isfile(path):
            raise SystemExit("ERROR: no netlist for unit %s at %s" % (u["unit"], path))
        lines = open(path, encoding="utf-8").read().split("\n")
        out.append((u, path, lines, parse_modules(lines)))
    return out


def comb_relations(mods, order, cells, rel):
    """Input port -> output ports reached through combinational logic only.

    Computed innermost first; an instance of a module defined here contributes
    its own relation, a Liberty cell contributes all-inputs-to-all-outputs
    unless it is sequential. Masks are Python integers over the input ports.
    """
    for name in order:
        mod = mods[name]
        inputs = [p for p, d in mod.ports.items() if d == "input"]
        outputs = [p for p, d in mod.ports.items() if d == "output"]
        index = {p: i for i, p in enumerate(inputs)}
        succ = defaultdict(list)
        for inst in mod.instances:
            cell = cells.get(inst.type)
            if cell is not None:
                if cell.sequential:
                    continue
                ins, outs = [], []
                for pin, (_, expr) in inst.pins.items():
                    bits = expr_bits(expr, mod.widths)
                    (outs if cell.pins.get(pin) == "output" else ins).extend(bits)
                for i in ins:
                    succ[i].extend(outs)
                continue
            sub = rel.get(inst.type)
            if sub is None:
                continue
            for pin_in, outs_ports in sub.items():
                if pin_in not in inst.pins:
                    continue
                src = expr_bits(inst.pins[pin_in][1], mod.widths)
                dst = [b for q in outs_ports if q in inst.pins
                       for b in expr_bits(inst.pins[q][1], mod.widths)]
                for i in src:
                    succ[i].extend(dst)
        for lhs, rhs in mod.assigns:
            lb, rb = expr_bits(lhs, mod.widths), expr_bits(rhs, mod.widths)
            if len(lb) == len(rb):
                for x, y in zip(rb, lb):
                    succ[x].append(y)
            else:
                for x in rb:
                    succ[x].extend(lb)

        mask: dict[str, int] = defaultdict(int)
        work = deque()
        for p in inputs:
            for b in expr_bits(p, mod.widths):
                mask[b] |= 1 << index[p]
                work.append(b)
        while work:
            n = work.popleft()
            m = mask[n]
            for s in succ.get(n, ()):
                if mask[s] | m != mask[s]:
                    mask[s] |= m
                    work.append(s)
        r = defaultdict(set)
        for q in outputs:
            m = 0
            for b in expr_bits(q, mod.widths):
                m |= mask.get(b, 0)
            for p, i in index.items():
                if m >> i & 1:
                    r[p].add(q)
        rel[name] = {p: sorted(qs) for p, qs in r.items()}


def clock_network(mod, seeds, cells):
    """Nets the seeds reach through combinational cells and assigns.

    A clock gate is a latch holding the enable and an AND of it with the
    clock; the AND output is on the network, the latch output is not.
    """
    fanout = defaultdict(list)
    for inst in mod.instances:
        cell = cells.get(inst.type)
        if cell is None or cell.sequential:
            continue
        outs = [e.strip() for pin, (_, expr) in inst.pins.items()
                if cell.pins.get(pin) == "output" for e in elements(expr)]
        for pin, (_, expr) in inst.pins.items():
            if cell.pins.get(pin) == "input":
                for e in elements(expr):
                    fanout[e.strip()].extend(outs)
    for lhs, rhs in mod.assigns:
        l, r = elements(lhs), elements(rhs)
        if len(l) == len(r):
            for a, b in zip(l, r):
                fanout[b.strip()].append(a.strip())
    net = set(seeds)
    work = deque(seeds)
    while work:
        for n in fanout.get(work.popleft(), ()):
            if n not in net:
                net.add(n)
                work.append(n)
    return net


def pin_classes(mods, order, cells):
    """(clock ports, async set/reset ports) of every module, innermost first."""
    clock, reset = {}, {}
    for name in order:
        mod = mods[name]
        cnets, rnets = set(), set()
        for inst in mod.instances:
            cell = cells.get(inst.type)
            for pin, (_, expr) in inst.pins.items():
                nets = {e.strip() for e in elements(expr)}
                if cell is not None:
                    if pin in cell.clock_pins:
                        cnets |= nets
                    elif pin in cell.async_pins:
                        rnets |= nets
                elif inst.type in clock:
                    if pin in clock[inst.type]:
                        cnets |= nets
                    elif pin in reset[inst.type]:
                        rnets |= nets
        ins = {p for p, d in mod.ports.items() if d == "input"}
        clock[name] = ins & cnets
        reset[name] = ins & rnets
    return clock, reset


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--units-dir", required=True)
    ap.add_argument("--top", required=True)
    ap.add_argument("--std-lib", required=True)
    ap.add_argument("--sram-lib-dir", required=True)
    ap.add_argument("--spec", required=True, help="sram.spec.json")
    ap.add_argument("--clock-port", action="append", default=[],
                    help="a clock port of the top (CLK_PORT); repeatable")
    ap.add_argument("--out", required=True)
    ap.add_argument("--blocks-json", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    plan = json.load(open(args.plan, encoding="utf-8"))
    spec = json.load(open(args.spec, encoding="utf-8"))
    cells = read_liberty([args.std_lib])
    cells.update(read_liberty(sorted(glob.glob(os.path.join(args.sram_lib_dir, "*.lib"))),
                              all_sequential=True))

    units = load_units(plan, args.units_dir)
    mods, owner = {}, {}
    for u, path, lines, modules in units:
        if u["unit"] not in {m.name for m in modules}:
            raise SystemExit("ERROR: %s does not define module %s" % (path, u["unit"]))
        for m in modules:
            if m.name in mods:
                raise SystemExit("ERROR: module %s is defined by both %s and %s"
                                 % (m.name, owner[m.name], path))
            mods[m.name] = m
            owner[m.name] = path
    errors = []

    # --- every type resolves; block pins match; inputs connected ----------
    local = {}
    for name, mod in mods.items():
        c = Counter(inst.type for inst in mod.instances)
        local[name] = c
        for t in c:
            if t not in cells and t not in mods:
                errors.append("%s instantiates %s, which is neither a cell, a macro "
                              "nor a module of the assembly" % (name, t))
        for inst in mod.instances:
            sub = mods.get(inst.type)
            if sub is None:
                continue
            extra = sorted(set(inst.pins) - set(sub.ports))
            if extra:
                errors.append("%s.%s connects %s, not ports of %s"
                              % (name, inst.name, ", ".join(extra), sub.name))
            open_in = sorted(p for p, d in sub.ports.items()
                             if d == "input" and p not in inst.pins)
            if open_in:
                errors.append("%s.%s leaves inputs of %s unconnected: %s"
                              % (name, inst.name, sub.name, ", ".join(open_in)))
    if args.top not in mods:
        errors.append("the top %s is not among the units" % args.top)
    if errors:
        raise SystemExit("ERROR: the assembled netlist is inconsistent:\n  "
                         + "\n  ".join(errors[:40]))

    # --- hierarchy: instances of every module and cell ---------------------
    order, seen = [], set()

    def post(name):
        if name in seen:
            return
        seen.add(name)
        for t in local[name]:
            if t in mods:
                post(t)
        order.append(name)
    post(args.top)
    mult = Counter({args.top: 1})
    total = Counter()
    for name in reversed(order):          # parents before children
        for t, n in local[name].items():
            if t in mods:
                mult[t] += mult[name] * n
            else:
                total[t] += mult[name] * n

    for m in spec["macros"]:
        if total[m["name"]] != m["instances"]:
            errors.append("macro %s: %d in the hierarchy, the SRAM spec expects %d"
                          % (m["name"], total[m["name"]], m["instances"]))
    unused = [u["unit"] for u, *_ in units if mult[u["unit"]] == 0]

    # --- clocks and resets across block boundaries --------------------------
    clock, reset = pin_classes(mods, order, cells)
    clocking = []          # (parent, instance, block, port, net, direct?)
    for name in order:
        mod = mods[name]
        # A block's clock ports are what its insides clock with; the top's are
        # only the ones named, since anything wired to a block clock would
        # otherwise count as one.
        seeds = (set(args.clock_port) if name == args.top and args.clock_port
                 else set(clock[name]))
        network = clock_network(mod, seeds, cells)
        for inst in mod.instances:
            if inst.type not in mods:
                continue
            for pin in sorted(clock[inst.type]):
                expr = inst.pins.get(pin, (0, ""))[1].strip()
                if expr not in network:
                    errors.append("%s.%s: clock port %s of %s is driven by %s, which the "
                                  "clock of %s does not reach through combinational "
                                  "logic" % (name, inst.name, pin, inst.type,
                                             expr or "nothing", name))
                else:
                    clocking.append((name, inst.name, inst.type, pin, expr, expr in seeds))
    if errors:
        raise SystemExit("ERROR: the assembled netlist is inconsistent:\n  "
                         + "\n  ".join(errors[:40]))

    # --- combinational paths through the blocks -----------------------------
    rel = {}
    comb_relations(mods, [n for n in order if n != args.top], cells, rel)

    # --- write -------------------------------------------------------------
    with open(args.out, "w", encoding="utf-8") as fh:
        for u, path, lines, _ in units:
            fh.write("\n".join(lines))
            if lines and lines[-1] != "":
                fh.write("\n")

    by_unit = {u["unit"]: u for u in plan["units"]}
    spec_of = {}
    for b in plan["blocks"]:
        for sp in b["specializations"]:
            spec_of[sp["unit"]] = (b["block"], sp)
    report_units = []
    for u, *_ in units:
        name = u["unit"]
        block, sp = spec_of.get(name, (None, None))
        report_units.append({
            "unit": name,
            "block": block,
            "ties": by_unit[name]["ties"],
            "sites": sp["sites"] if sp else [],
            "instances_in_design": mult[name],
            "cells": sum(n for t, n in local[name].items() if t in cells),
            "children": {t: n for t, n in local[name].items() if t in mods},
            "clock_ports": sorted(clock.get(name, ())),
            "async_reset_ports": sorted(reset.get(name, ())),
            "comb_paths": rel.get(name, {}),
        })
    json.dump({"top": args.top, "units": report_units, "unused_units": unused,
               "macros": {m["name"]: total[m["name"]] for m in spec["macros"]}},
              open(args.blocks_json, "w", encoding="utf-8"), indent=1)

    lines = ["Block synthesis: %d units" % len(units), "=" * 72,
             "%-34s %6s %9s  %s" % ("unit", "inst", "cells", "ties")]
    for r in report_units:
        ties = ", ".join("%s=%s" % kv for kv in sorted(r["ties"].items())) or "-"
        lines.append("%-34s %6d %9d  %s" % (r["unit"], r["instances_in_design"],
                                           r["cells"], ties))
    lines += ["", "Clock and asynchronous set/reset ports of every block", "-" * 72]
    for r in report_units:
        if r["block"]:
            lines.append("%-34s clock: %s; async reset: %s"
                         % (r["unit"], ", ".join(r["clock_ports"]) or "-",
                            ", ".join(r["async_reset_ports"]) or "-"))
    lines += ["", "Block clocks, per parent: direct from the parent's clock, or through "
              "its clock gating", "-" * 72]
    seen_kinds = defaultdict(Counter)
    for parent, _, block, pin, net, direct in clocking:
        seen_kinds[(parent, block, pin)]["direct" if direct else "gated by " + net] += 1
    for (parent, block, pin), kinds in sorted(seen_kinds.items()):
        lines.append("%-24s %-30s %s" % (parent, "%s.%s" % (block, pin),
                     ", ".join("%s x%d" % kv for kv in sorted(kinds.items()))))
    lines += ["", "Combinational input -> output paths through a block", "-" * 72]
    for r in report_units:
        if r["block"]:
            paths = r["comb_paths"]
            if not paths:
                lines.append("%-34s none" % r["unit"])
                continue
            lines.append("%-34s %d input(s):" % (r["unit"], len(paths)))
            for p, qs in sorted(paths.items()):
                lines.append("    %s -> %s" % (p, ", ".join(qs)))
    if unused:
        lines += ["", "Units not instantiated by the top: " + ", ".join(unused)]
    lines += ["", "Macros in the hierarchy: " + ", ".join(
        "%s x%d" % (m["name"], total[m["name"]]) for m in spec["macros"])]
    text = "\n".join(lines) + "\n"
    open(args.report, "w", encoding="utf-8").write(text)
    sys.stdout.write(text)
    if unused:
        print("  warning: units never instantiated: %s" % ", ".join(unused),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
