#!/usr/bin/env python3
"""Insert buffer trees on high-fanout nets of a Yosys/ABC mapped netlist.

Yosys' ABC pass buffers internal fanout but not nets that reach the boundary of
its combinational partition.  Every flip-flop input is such a boundary, so a
control signal that clears or enables tens of thousands of registers ends up on
a single buffer: in AmeB8OOO `ameAdapter.io_cancel` drives 14091 pins and the
synchronized reset drives every register, and OpenSTA charges tens of
nanoseconds to them.  A place-and-route tool fixes this with fanout repair;
Synthesis-Flow-Kit's own scripts/fix-fanout.tcl does it with iEDA, which this
flow does not require, so this does the same job directly on the netlist.

The cell library is read for pin directions and for which pin of each cell is
its clock, so nothing here is specific to one PDK.

Clock nets are left alone: they are analysed as ideal clocks, and buffering them
is clock-tree synthesis, not fanout repair.

A hierarchical netlist (block synthesis, or FLATTEN=0) is repaired one module
at a time, each tree inside the module that owns the net. An instance of a
module defined in the same file loads a net like a cell pin does, including
through a `{ a , b }` concatenation on a bus port; a port of such a module is a
clock port when, inside it, it reaches a cell's clock pin, and nets driving it
are then clock nets in the parent too. A block keeps its ports as buses, so a
bit of one (`io_x[3]`) is a load's net too. A single-module netlist comes out
exactly as it always did.

  fix_fanout.py <in.v> <out.v> <liberty> [--max-fanout N] [--buffer CELL]
"""
import argparse
import re
import sys

from netlist_lib import elements, parse_modules, single_bit, verilog_name

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('liberty')
ap.add_argument('--max-fanout', type=int, default=24)
ap.add_argument('--buffer', required=True,
                help='buffer cell to build the trees from; its input and output '
                     'pin names are read out of the Liberty')
a = ap.parse_args()

# --- pin directions and clock pins, from the Liberty -----------------------
direction, clockpin = {}, {}
cell = None
for line in open(a.liberty):
    m = re.match(r'\s*cell\s*\((\S+?)\)', line)
    if m: cell = m.group(1).strip(chr(34)); continue
    m = re.match(r'\s*(?:pin|bus)\s*\((\S+?)\)', line)
    if m and cell: pin = m.group(1).strip(chr(34)); continue
    m = re.match(r'\s*direction\s*:\s*"?(\w+)', line)
    if m and cell: direction[(cell, pin)] = m.group(1)
    # A clock pin is an input marked `clock : true` (CK on DFFHx1/DFFHQNx1/DHLx1).
    # Asynchronous SET/RESET also carry min_pulse_width arcs, so that is not a marker.
    if re.match(r'\s*clock\s*:\s*"?true', line) and cell:
        clockpin[(cell, pin)] = True

# The buffer's own pin names come from the same Liberty, so nothing here is
# tied to one library's naming (BUFx2 .A/.Y on USC-3N-2D, BUF_X2 .A/.Z on
# Nangate45).
buf_in = [pin for (c, pin), d in direction.items() if c == a.buffer and d == 'input']
buf_out = [pin for (c, pin), d in direction.items() if c == a.buffer and d == 'output']
if len(buf_in) != 1 or len(buf_out) != 1:
    sys.exit('%s: %s is not a single-input, single-output cell in %s (in=%s out=%s)'
             % (sys.argv[0], a.buffer, a.liberty, buf_in, buf_out))
BUF_IN, BUF_OUT = buf_in[0], buf_out[0]

lines = open(a.src).read().split('\n')
modules = parse_modules(lines)
if not modules:
    sys.exit('%s: no module in %s' % (sys.argv[0], a.src))
defined = {m.name: m for m in modules}

# --- clock ports of the modules defined here, innermost first ---------------
# A port is a clock port if the net it names drives a cell clock pin, or a
# clock port of an instance, inside its module. Iterate to a fixed point so
# the order of the modules in the file does not matter.
clock_ports = {m.name: set() for m in modules}
changed = True
while changed:
    changed = False
    for mod in modules:
        nets = set()
        for inst in mod.instances:
            for pin, (_, expr) in inst.pins.items():
                if clockpin.get((inst.type, pin)) or pin in clock_ports.get(inst.type, ()):
                    nets.update(e.strip() for e in elements(expr))
        found = {p for p, d in mod.ports.items() if d == 'input' and p in nets}
        if not found <= clock_ports[mod.name]:
            clock_ports[mod.name] |= found
            changed = True

counter = [0]


def fresh():
    counter[0] += 1
    return '_fanout_%06d_' % counter[0]


def repair(mod):
    """Buffer trees for one module; returns (wires, cells, remap, big)."""
    sinks = {}          # net -> [(line index, element index or None)]
    drives_clock = set()
    for inst in mod.instances:
        sub = defined.get(inst.type)
        for pin, (i, expr) in inst.pins.items():
            key = (inst.type, pin)
            if sub is None:
                expr = expr.strip()
                if clockpin.get(key):
                    drives_clock.add(expr)
                # A bit of a bus that was not split, as a block's own input
                # ports are (`io_x[3]`), is a net like any other; the flat
                # flow splits every bus, so it never meets one.
                elif direction.get(key) == 'input' and (re.fullmatch(r'[\w$]+(\[\d+\])?', expr) or (expr.startswith('\\') and ' ' not in expr)):
                    sinks.setdefault(expr, []).append((i, None))
                continue
            els = elements(expr)
            if pin in clock_ports[sub.name]:
                drives_clock.update(e.strip() for e in els)
                continue
            if sub.ports.get(pin) != 'input':
                continue
            concat = expr.strip().startswith('{')
            for k, e in enumerate(els):
                net = single_bit(e, mod.widths)
                if net is not None:
                    sinks.setdefault(net, []).append((i, k if concat else None))

    big = {net: ls for net, ls in sinks.items()
           if len(ls) > a.max_fanout and net not in drives_clock}

    new_wires, new_cells, remap = [], [], {}

    def assign(net, ls):
        if len(ls) <= a.max_fanout:
            for j in ls: remap[j] = net
            return
        k = min(a.max_fanout, max(2, -(-len(ls) // a.max_fanout)))
        step = -(-len(ls) // k)
        for g in range(0, len(ls), step):
            child = fresh()
            new_wires.append(child)
            new_cells.append('  %s %s (\n    .%s(%s),\n    .%s(%s)\n  );'
                             % (a.buffer, fresh().replace('_fanout_', '_fanbuf_'),
                                BUF_IN, verilog_name(net), BUF_OUT, child))
            assign(child, ls[g:g + step])

    for net, ls in big.items():
        assign(net, ls)
    return new_wires, new_cells, remap, big


results = [(mod, repair(mod)) for mod in modules]

nbig = sum(len(r[3]) for _, r in results)
print('nets above fanout %d: %d' % (a.max_fanout, nbig), file=sys.stderr)
for mod, (_, _, _, big) in results:
    if len(modules) > 1 and big:
        print('  module %s: %d' % (mod.name, len(big)), file=sys.stderr)
    for net in sorted(big, key=lambda x: -len(big[x]))[:8]:
        print('   %-40s %d' % (net, len(big[net])), file=sys.stderr)

# --- rewrite the loads -----------------------------------------------------
# A whole-expression load gets its expression replaced; a load inside a
# concatenation gets its element replaced, the rest of the concatenation kept.
by_line = {}
for _, (_, _, remap, _) in results:
    for (j, k), net in remap.items():
        by_line.setdefault(j, {})[k] = net
for j, repl in by_line.items():
    if None in repl:
        net = repl[None]
        lines[j] = re.sub(r'\((.*?)\)(,?)$', lambda m: '(%s)%s' % (net, m.group(2)), lines[j])
        continue
    m = re.match(r'^(    \.\w+\()(.*)(\),?)$', lines[j])
    els = elements(m.group(2))
    for k, net in repl.items():
        els[k] = net
    # ' , ' keeps the space that must end every escaped identifier.
    lines[j] = '%s{ %s }%s' % (m.group(1), ' , '.join(els), m.group(3))

# --- insert the trees, each before its own module's endmodule --------------
for mod, (new_wires, new_cells, _, _) in sorted(results, key=lambda r: -r[0].end):
    insert = ['  wire %s;' % w for w in new_wires] + new_cells
    lines[mod.end:mod.end] = insert
open(a.dst, 'w').write('\n'.join(lines))
ncells = sum(len(r[1]) for _, r in results)
print('inserted %d buffers on %d nets' % (ncells, nbig), file=sys.stderr)
