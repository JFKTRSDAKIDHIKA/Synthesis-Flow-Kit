#!/usr/bin/env python3
"""Plan hierarchical synthesis: which units are synthesized, and how.

    block_plan.py --rtl <top.sram.sv> --top AMEOOO --blocks "A B C" --out plan.json

Every block named in --blocks is synthesized on its own, once per distinct
specialization, and instantiated as a black box everywhere else, the top
included; the netlists are then put back together (assemble_netlist.py). A
block is a module name of the emitted RTL. Nesting is allowed: a block inside
another block is a black box there too.

Specialization. Synthesizing a module once for all its instances gives up the
constant propagation a flat run would do across its boundary. The one kind of
constant this plan recovers is the literal tie at an instantiation site, e.g.
the matrix array's `.op_i(3'h7)`: instances are grouped by the set of input
ports they tie to literals, and each group is its own unit, synthesized with
those ports driven by their constants inside it. The ports stay on the unit's
interface, unconnected, so the parent's connections do not change; the parent's
instances of the group are retyped to the unit by the source line of their
instantiation (`src` attribute). A group with no ties is the block itself.

Parameters. A unit is elaborated from the RTL module with its default
parameters. A parameterized block whose instances override parameters would
come out wrong, so this stops on it rather than guess; firtool emits no
parameters, so the check only ever concerns hand-written modules.

The plan also lists, per unit, the other blocks to black-box and the Yosys
commands that retype their specialized instances, so the per-unit Yosys script
is a pure function of the plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

LITERAL_RE = re.compile(r"^(\d+)'([bBhHdDoO])([0-9a-fA-F_]+)$")


def literal_bits(text: str) -> str | None:
    """A sized, fully specified literal as a Yosys constant (`3'b111`)."""
    m = LITERAL_RE.match(text.strip())
    if m is None:
        return None
    width = int(m.group(1))
    base = {"b": 2, "h": 16, "d": 10, "o": 8}[m.group(2).lower()]
    value = int(m.group(3).replace("_", ""), base)
    if value >= 1 << width:
        return None
    return "%d'b%s" % (width, format(value, "0%db" % width))


def match_paren(text: str, open_at: int) -> int:
    """Index of the parenthesis closing the one at open_at."""
    depth = 0
    for i in range(open_at, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    raise SystemExit("ERROR: unbalanced parentheses from offset %d" % open_at)


def connections(body: str) -> dict[str, str]:
    """Named port connections `.port (expr)` of an instance, by port."""
    out: dict[str, str] = {}
    i = 0
    while True:
        m = re.compile(r"\.\s*(\w+)\s*\(").search(body, i)
        if m is None:
            return out
        close = match_paren(body, m.end() - 1)
        out[m.group(1)] = body[m.end():close].strip()
        i = close + 1


def module_starts(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1)) for m in re.finditer(r"^module\s+(\w+)", text, re.M)]


def enclosing(starts: list[tuple[int, str]], pos: int) -> str:
    name = "?"
    for s, n in starts:
        if s > pos:
            break
        name = n
    return name


def sites_of(text: str, block: str, starts) -> list[dict]:
    """Every instantiation of `block`: line of the instance name, ties, parent."""
    out = []
    pat = re.compile(r"^[ \t]*%s\b(\s*#\s*\()?" % re.escape(block), re.M)
    for m in pat.finditer(text):
        pos = m.end()
        if m.group(1):
            pos = match_paren(text, m.end() - 1) + 1
        name = re.compile(r"\s*(\\\S+|[A-Za-z_][\w$]*)\s*(\[[^\]]*\]\s*)?\(").match(text, pos)
        if name is None:
            continue           # a declaration or another use of the name
        open_at = name.end() - 1
        body = text[open_at + 1 : match_paren(text, open_at)]
        ties = {}
        for port, expr in connections(body).items():
            bits = literal_bits(expr)
            if bits is not None:
                ties[port] = bits
        out.append({
            "line": text.count("\n", 0, name.start(1)) + 1,
            "instance": name.group(1),
            "parent": enclosing(starts, m.start()),
            "overrides_parameters": bool(m.group(1)),
            "ties": ties,
        })
    return out


def tie_tag(ties: dict[str, str]) -> str:
    key = ";".join("%s=%s" % kv for kv in sorted(ties.items()))
    return hashlib.sha1(key.encode()).hexdigest()[:6]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtl", required=True, help="the RTL every unit reads")
    ap.add_argument("--top", required=True)
    ap.add_argument("--blocks", required=True, help="space-separated module names")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    blocks = args.blocks.split()
    if len(set(blocks)) != len(blocks):
        raise SystemExit("ERROR: SYNTH_BLOCKS names a module twice: %s" % args.blocks)
    if args.top in blocks:
        raise SystemExit("ERROR: the top %s cannot also be a block" % args.top)

    text = open(args.rtl, encoding="utf-8", errors="replace").read()
    starts = module_starts(text)
    rtl_base = os.path.basename(args.rtl)

    plan_blocks = []
    for block in blocks:
        defs = [m for m in re.finditer(r"^module\s+%s\b(\s*#\s*\()?" % re.escape(block),
                                       text, re.M)]
        if len(defs) != 1:
            raise SystemExit("ERROR: SYNTH_BLOCKS names %s, which %s has %d definitions of"
                             % (block, args.rtl, len(defs)))
        sites = sites_of(text, block, starts)
        if not sites:
            raise SystemExit("ERROR: SYNTH_BLOCKS names %s, which is never instantiated in %s"
                             % (block, args.rtl))
        overridden = [s for s in sites if s["overrides_parameters"]]
        if defs[0].group(1) and overridden:
            raise SystemExit(
                "ERROR: %s is parameterized and %d of its instantiations override "
                "parameters (first at %s:%d). Synthesizing it once with its defaults "
                "would be wrong; take it out of SYNTH_BLOCKS."
                % (block, len(overridden), rtl_base, overridden[0]["line"]))

        groups: dict[str, dict] = {}
        for s in sites:
            tag = tie_tag(s["ties"]) if s["ties"] else ""
            g = groups.setdefault(tag, {"ties": s["ties"], "sites": []})
            g["sites"].append(s)
        specs = []
        for tag, g in sorted(groups.items()):
            specs.append({
                "unit": block if not tag else "%s__t%s" % (block, tag),
                "ties": g["ties"],
                "sites": [{"line": s["line"], "parent": s["parent"],
                           "instance": s["instance"]} for s in g["sites"]],
            })
        plan_blocks.append({"block": block, "specializations": specs})

    # Retyping commands, shared by every unit: each specialized site's
    # instances become the specialized unit, then no instance of the block may
    # be left outside the sites that really use the plain block.
    retype = []
    for b in plan_blocks:
        plain_lines = [s["line"] for sp in b["specializations"] if not sp["ties"]
                       for s in sp["sites"]]
        for sp in b["specializations"]:
            if not sp["ties"]:
                continue
            for s in sp["sites"]:
                retype.append("chtype -set %s t:%s a:src=*%s:%d.* %%i"
                              % (sp["unit"], b["block"], rtl_base, s["line"]))
        rest = "t:%s" % b["block"] + "".join(" a:src=*%s:%d.* %%d" % (rtl_base, l)
                                             for l in plain_lines)
        retype.append("select -assert-none %s" % rest)

    units = [{"unit": args.top, "rtl_top": args.top, "ties": {},
              "blackboxes": blocks, "top": True}]
    for b in plan_blocks:
        for sp in b["specializations"]:
            units.append({"unit": sp["unit"], "rtl_top": b["block"], "ties": sp["ties"],
                          "blackboxes": [x for x in blocks if x != b["block"]],
                          "top": False})

    plan = {"top": args.top, "rtl": args.rtl, "blocks": plan_blocks,
            "units": units, "retype": retype}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=1)
        fh.write("\n")

    for b in plan_blocks:
        for sp in b["specializations"]:
            ties = ", ".join("%s=%s" % kv for kv in sorted(sp["ties"].items())) or "-"
            parents = sorted({s["parent"] for s in sp["sites"]})
            print("  %-34s %3d site(s) in %s; ties: %s"
                  % (sp["unit"], len(sp["sites"]), ", ".join(parents), ties))
    print("  %d units: the top and %d block specialization(s)"
          % (len(units), len(units) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
