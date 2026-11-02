"""Readers for the Liberty files and the Yosys-written gate-level netlists.

Shared by fix_fanout.py and assemble_netlist.py. The netlist reader handles
exactly what `write_verilog -noattr -noexpr -nohex -nodec` emits: one port or
wire declaration per line, one instance per block of lines

    TYPE NAME (
      .PIN(EXPR),
      ...
    );

with NAME possibly escaped (`\\a.b ` -- an escaped identifier ends at a space),
EXPR a net, a bit-select, a constant or a `{ a , b }` concatenation, and
`assign LHS = RHS;` statements. Anything else in a module body is ignored.
"""

from __future__ import annotations

import re

INSTANCE_RE = re.compile(r"^  (\S+) (\\\S+ |[^\s\\]\S*) ?\($")
PIN_RE = re.compile(r"^    \.(\w+)\((.*)\),?$")
DECL_RE = re.compile(r"^  (input|output|inout) (?:\[(\d+):(\d+)\] )?(\\\S+ |\S+);$")
WIRE_RE = re.compile(r"^  wire (?:\[(\d+):(\d+)\] )?(\\\S+ |\S+);$")
ASSIGN_RE = re.compile(r"^  assign (.*) = (.*);$")
CONST_RE = re.compile(r"^\d+'[bhdo][0-9a-fA-FxXzZ_]+$")
RANGE_RE = re.compile(r"^([^\\\s][^\[\s]*)\[(\d+):(\d+)\]$")


# --------------------------------------------------------------------------
# Liberty


class Cell:
    __slots__ = ("name", "pins", "clock_pins", "async_pins", "sequential")

    def __init__(self, name: str):
        self.name = name
        self.pins: dict[str, str] = {}
        self.clock_pins: set[str] = set()
        self.async_pins: set[str] = set()   # the clear/preset of an ff or latch
        self.sequential = False


def read_liberty(paths, all_sequential: bool = False) -> dict[str, Cell]:
    """Cells of the given Liberty files: pin directions, clock and async pins, state.

    A cell is sequential if it has an ff, latch or memory group. With
    all_sequential every cell is taken as sequential, which is how the SRAM
    macros are treated: their outputs are registered, so no combinational arc
    crosses them.
    """
    cells: dict[str, Cell] = {}
    for path in paths:
        cell = pin = None
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*cell\s*\(\s*\"?([^\s\")]+)\"?\s*\)", line)
            if m:
                cell = cells.setdefault(m.group(1), Cell(m.group(1)))
                cell.sequential = cell.sequential or all_sequential
                pin = None
                continue
            if cell is None:
                continue
            m = re.match(r"\s*(?:pin|bus)\s*\(\s*\"?([^\s\")]+)\"?\s*\)", line)
            if m:
                pin = m.group(1)
                continue
            if re.match(r"\s*(?:ff|latch|ff_bank|latch_bank|memory)\s*\(", line):
                cell.sequential = True
            m = re.match(r'\s*(?:clear|preset)\s*:\s*"([^"]*)"', line)
            if m:
                cell.async_pins.update(re.findall(r"[A-Za-z_]\w*", m.group(1)))
            m = re.match(r"\s*direction\s*:\s*(\w+)", line)
            if m and pin is not None:
                cell.pins[pin] = m.group(1)
            # A clock pin is an input marked `clock : true`. Asynchronous
            # SET/RESET also carry min_pulse_width arcs, so that is no marker.
            if re.match(r"\s*clock\s*:\s*true", line) and pin is not None:
                cell.clock_pins.add(pin)
    return cells


# --------------------------------------------------------------------------
# Netlist


class Instance:
    __slots__ = ("type", "name", "line", "pins")

    def __init__(self, type_: str, name: str, line: int):
        self.type = type_
        self.name = name
        self.line = line
        # pin -> (line index, expression text)
        self.pins: dict[str, tuple[int, str]] = {}


class Module:
    __slots__ = ("name", "start", "end", "ports", "widths", "instances", "assigns")

    def __init__(self, name: str, start: int):
        self.name = name
        self.start = start          # line index of `module`
        self.end = start            # line index of `endmodule`
        self.ports: dict[str, str] = {}          # port -> direction
        self.widths: dict[str, tuple[int, int]] = {}   # net -> (msb, lsb)
        self.instances: list[Instance] = []
        self.assigns: list[tuple[str, str]] = []


def strip_name(token: str) -> str:
    """An identifier as it appears in a declaration, without the escape space."""
    return token.strip()


def parse_modules(lines: list[str]) -> list[Module]:
    modules: list[Module] = []
    mod = inst = None
    for i, line in enumerate(lines):
        if line.startswith("module "):
            name = line[len("module "):].split("(", 1)[0].strip()
            mod = Module(name, i)
            modules.append(mod)
            inst = None
            continue
        if mod is None:
            continue
        if line.startswith("endmodule"):
            mod.end = i
            mod = inst = None
            continue
        if inst is not None:
            m = PIN_RE.match(line)
            if m:
                inst.pins[m.group(1)] = (i, m.group(2))
                continue
            if line.startswith("  );"):
                inst = None
            continue
        m = INSTANCE_RE.match(line)
        if m:
            inst = Instance(m.group(1), strip_name(m.group(2)), i)
            mod.instances.append(inst)
            continue
        m = DECL_RE.match(line)
        if m:
            net = strip_name(m.group(4))
            mod.ports[net] = m.group(1)
            if m.group(2) is not None:
                mod.widths[net] = (int(m.group(2)), int(m.group(3)))
            continue
        m = WIRE_RE.match(line)
        if m:
            if m.group(1) is not None:
                mod.widths[strip_name(m.group(3))] = (int(m.group(1)), int(m.group(2)))
            continue
        m = ASSIGN_RE.match(line)
        if m:
            mod.assigns.append((m.group(1), m.group(2)))
    return modules


def elements(expr: str) -> list[str]:
    """The top-level elements of an expression, MSB first, as written."""
    expr = expr.strip()
    if expr.startswith("{") and expr.endswith("}"):
        return [e.strip() for e in split_concat(expr[1:-1])]
    return [expr]


def split_concat(body: str) -> list[str]:
    """Split a concatenation body on commas; escaped names end at a space."""
    out, cur, i = [], [], 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            j = body.find(" ", i)
            j = len(body) if j < 0 else j
            cur.append(body[i:j + 1])
            i = j + 1
            continue
        if c == ",":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def element_bits(element: str, widths: dict[str, tuple[int, int]]) -> list[str] | None:
    """The single-bit nets an element stands for, MSB first.

    None for a constant. A name without a select stands for all its bits.
    """
    e = element.strip()
    if not e or CONST_RE.match(e):
        return None
    if e.startswith("\\"):
        return [e]
    m = RANGE_RE.match(e)
    if m:
        name, hi, lo = m.group(1), int(m.group(2)), int(m.group(3))
        step = -1 if hi >= lo else 1
        return ["%s[%d]" % (name, b) for b in range(hi, lo + step, step)]
    if "[" in e:
        return [e]
    if e in widths:
        hi, lo = widths[e]
        step = -1 if hi >= lo else 1
        return ["%s[%d]" % (e, b) for b in range(hi, lo + step, step)]
    return [e]


def expr_bits(expr: str, widths: dict[str, tuple[int, int]]) -> list[str]:
    bits: list[str] = []
    for e in elements(expr):
        b = element_bits(e, widths)
        if b:
            bits.extend(b)
    return bits


def single_bit(element: str, widths: dict[str, tuple[int, int]]) -> str | None:
    """The net an element names if it is exactly one bit, else None."""
    e = element.strip()
    if not e or CONST_RE.match(e):
        return None
    if e.startswith("\\"):
        return e if " " not in e else None
    if RANGE_RE.match(e):
        return None
    if "[" in e:
        return e
    if e in widths:
        hi, lo = widths[e]
        return e if hi == lo else None
    return e if re.fullmatch(r"[\w$]+", e) else None


def verilog_name(net: str) -> str:
    """A net as it must be written: escaped identifiers keep their terminating space."""
    return net + " " if net.startswith("\\") else net
