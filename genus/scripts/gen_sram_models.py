#!/usr/bin/env python3
"""Generate the SRAM macro models a configuration needs, with FakeRAM2.0.

    hardware config -> sram_spec.py -> FakeRAM2.0 -> Liberty / LEF / Verilog
                                                  -> Yosys + OpenSTA

FakeRAM2.0 (third_party/FakeRAM2.0) is a memory compiler stand-in: given a
process description and a list of (depth, width, banks), it produces the
Liberty, LEF and Verilog views of a single-port macro. This script drives it
from the spec that sram_spec.py read out of the RTL, so the macro set follows
the hardware configuration with nothing to maintain by hand.

Three things are done around FakeRAM:

  * the process description is completed per macro. FakeRAM spreads the array
    over `banks` and `column_mux_factor`, which changes the macro's shape but
    not its area; the pair is chosen here so that each macro comes out roughly
    square *and* tall enough to fit its own pins, which FakeRAM's LEF writer
    requires and otherwise exits over.

  * the optional timing block of the process description replaces the fixed
    access time, cycle time, setup, hold and pin capacitance that FakeRAM2.0
    hard-codes in utils/class_memory.py for a 7 nm process. Without it a 3 nm
    macro would be handed 7 nm numbers.

  * the per-bit write mask is added back to the Liberty and to the blackbox
    Verilog. CoralNPU's `Sram` wrapper writes byte-masked, and FakeRAM2.0 has
    the `w_mask_in` pin commented out in utils/create_lib.py and
    utils/create_verilog.py. The pin is emitted here with the same constraint
    template, setup/hold and capacitance FakeRAM gives the write data, so the
    mask is timed rather than dropped.

  * one-read-one-write macros are derived from the single-port ones. FakeRAM2.0
    only builds 1RW memories; a `1r1w` macro in the spec is built as 1RW and
    its Liberty then rewritten to two ports: `addr_in` becomes `raddr_in` and
    `waddr_in` with the same constraints, `ce_in` becomes the read enable
    `re_in`, `we_in` stays the write enable, and the cell area is multiplied
    by --area-factor-2p. A two-port bitcell is larger than a single-port one
    and FakeRAM has no model of it, so that factor is an explicit assumption;
    at the default 1.0 the two-port macros are priced as single-port ones,
    which is optimistic. No LEF is written for them.

Nothing is written outside --out-dir; FakeRAM2.0 itself is never modified.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys


def load_fakeram(fakeram_dir: str):
    """Import FakeRAM2.0 from the submodule."""
    if not os.path.isfile(os.path.join(fakeram_dir, "run.py")):
        raise SystemExit(
            "ERROR: FakeRAM2.0 is not checked out at %s\n"
            "       run: git submodule update --init --recursive" % fakeram_dir
        )
    sys.path.insert(0, fakeram_dir)
    from utils.area import get_macro_dimensions           # noqa: E402
    from utils.class_memory import Memory                 # noqa: E402
    from utils.class_process import Process               # noqa: E402
    from utils.create_lef import create_lef               # noqa: E402
    from utils.create_lib import create_lib               # noqa: E402
    from utils.create_verilog import create_verilog       # noqa: E402
    return dict(get_macro_dimensions=get_macro_dimensions, Memory=Memory,
                Process=Process, create_lef=create_lef, create_lib=create_lib,
                create_verilog=create_verilog)


# FakeRAM's LEF writer puts every signal pin on one vertical track column, and
# exits if the macro is not tall enough to hold them. Mirror the count from
# utils/create_lef.py so that a shape can be rejected before it is built. The
# write mask adds `width` pins on top of what FakeRAM counts.
def pin_tracks_needed(width: int, addr_bits: int, with_mask: bool,
                      ports: str = "1rw") -> int:
    second_address = addr_bits if ports == "1r1w" else 0
    return 2 * width + addr_bits + second_address + 3 + (width if with_mask else 0)


def shape(fr, process, depth: int, width: int, banks: int):
    """The (height, width) in um FakeRAM would give this macro, after snapping."""
    h, w = fr["get_macro_dimensions"](process, {"width": width, "depth": depth,
                                                "banks": banks})
    snap = lambda v, nm: math.ceil(v * 1000.0 / nm) * nm / 1000.0
    return snap(h, process.snap_height_nm), snap(w, process.snap_width_nm)


def choose_shape(fr, process_json, macro):
    """Pick (banks, column_mux_factor) for one macro.

    Both only redistribute the array between height and width; the area is
    identical for every choice. Prefer the squarest macro that still has room
    for its pins, and fall back to the process default when none has.
    """
    depth, width = macro["depth"], macro["width"]
    needed = pin_tracks_needed(width, macro["addr_bits"], with_mask=True,
                               ports=macro.get("ports", "1rw"))
    default = (macro["banks"], int(process_json["column_mux_factor"]))

    best = None
    for banks in (1, 2, 4):
        if banks > 1 and depth % banks:
            continue
        for cmux in (1, 2, 4, 8):
            if depth % (banks * cmux):
                continue
            process = fr["Process"](dict(process_json, column_mux_factor=cmux))
            h, w = shape(fr, process, depth, width, banks)
            pitch = process.pin_pitch_um
            tracks = math.floor((h - 2 * pitch) / pitch)
            if tracks < needed:
                continue
            score = abs(math.log(h / w))
            if best is None or score < best[0]:
                best = (score, banks, cmux)
    if best is None:
        return default[0], default[1], False
    return best[1], best[2], True


def apply_overrides(mem, process_json):
    """Replace FakeRAM2.0's hard-coded 7 nm timing with the PDK's own."""
    timing = process_json.get("timing_ns") or {}
    caps = process_json.get("capacitance_pf") or {}
    if "access_time" in timing:
        mem.access_time_ns = float(timing["access_time"])
    if "cycle_time" in timing:
        mem.cycle_time_ns = float(timing["cycle_time"])
    if "setup" in timing:
        mem.t_setup_ns = float(timing["setup"])
    if "hold" in timing:
        mem.t_hold_ns = float(timing["hold"])
    if "input_pin" in caps:
        mem.cap_input_pf = float(caps["input_pin"])
    if "leakage_mw_per_bank" in process_json:
        mem.standby_leakage_per_bank_mW = float(process_json["leakage_mw_per_bank"])
    # FakeRAM derives the NLDM table indices from fo4; scale it with the
    # process so that a 3 nm macro is not characterized over a 7 nm slew range.
    if "fo4_ps" in process_json:
        mem.fo4_ps = float(process_json["fo4_ps"])


def mask_pin_liberty(mem) -> str:
    """The `w_mask_in` bus, in the shape utils/create_lib.py would have written.

    FakeRAM2.0 carries this pin commented out. The values are exactly the ones
    it gives `wd_in`: same constraint template, same setup and hold, same
    capacitance, so the mask is constrained like any other write-side input.
    """
    name = mem.name
    tsetup, thold = mem.t_setup_ns, mem.t_hold_ns
    cap = mem.cap_input_pf
    pindynamic = mem.pin_dynamic_power_mW * 1e1
    fo4 = mem.fo4_ps / 1e3
    slew = "%.3f, %.3f" % (1 * fo4, 25 * fo4)

    def constraint(kind, value):
        return (
            "        timing() {\n"
            "            related_pin : clk;\n"
            "            timing_type : %s ;\n"
            "            rise_constraint(%s_constraint_template) {\n"
            '                index_1 ("%s");\n'
            '                index_2 ("%s");\n'
            "                values ( \\\n"
            '                  "%.3f, %.3f", \\\n'
            '                  "%.3f, %.3f" \\\n'
            "                )\n"
            "            }\n"
            "            fall_constraint(%s_constraint_template) {\n"
            '                index_1 ("%s");\n'
            '                index_2 ("%s");\n'
            "                values ( \\\n"
            '                  "%.3f, %.3f", \\\n'
            '                  "%.3f, %.3f" \\\n'
            "                )\n"
            "            }\n"
            "        }\n"
        ) % (kind, name, slew, slew, value, value, value, value,
             name, slew, slew, value, value, value, value)

    return (
        "    bus(w_mask_in)   {\n"
        "        bus_type : %s_DATA;\n"
        "        direction : input;\n"
        "        capacitance : %.3f;\n"
        "        memory_write() {\n"
        "            address : addr_in;\n"
        '            clocked_on : "clk";\n'
        "        }\n"
        "%s%s"
        "        internal_power(){\n"
        "            rise_power(%s_energy_template_sigslew) {\n"
        '                index_1 ("%s");\n'
        '                values ("%.3f, %.3f")\n'
        "            }\n"
        "            fall_power(%s_energy_template_sigslew) {\n"
        '                index_1 ("%s");\n'
        '                values ("%.3f, %.3f")\n'
        "            }\n"
        "        }\n"
        "    }\n"
    ) % (name, cap, constraint("setup_rising", tsetup), constraint("hold_rising", thold),
         name, slew, pindynamic, pindynamic, name, slew, pindynamic, pindynamic)


def patch_liberty(path: str, mem) -> None:
    """Insert the write-mask pin into the Liberty FakeRAM just wrote."""
    text = open(path, encoding="utf-8").read()
    anchor = "    cell_leakage_power :"
    if anchor not in text:
        raise SystemExit(
            "ERROR: %s does not end the way FakeRAM2.0's create_lib writes it; "
            "the write-mask pin cannot be placed. Check the submodule version."
            % path
        )
    head, _, tail = text.partition(anchor)
    open(path, "w", encoding="utf-8").write(head + mask_pin_liberty(mem)
                                            + anchor + tail)


def find_group(text: str, kind: str, name: str) -> tuple[int, int]:
    """(start, end) of the Liberty group `kind(name) { ... }`, whole lines."""
    m = re.search(r"^[ \t]*%s\(%s\)\s*\{" % (kind, name), text, re.M)
    if m is None:
        raise SystemExit("ERROR: no %s(%s) group in the FakeRAM Liberty" % (kind, name))
    depth = 0
    for i in range(m.end() - 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = text.index("\n", i) + 1
                return m.start(), end
    raise SystemExit("ERROR: unbalanced %s(%s) group in the FakeRAM Liberty" % (kind, name))


def to_two_port(path: str, area_factor: float) -> float:
    """Rewrite a 1RW FakeRAM Liberty as a 1R1W one. Returns the new cell area."""
    text = open(path, encoding="utf-8").read()

    start, end = find_group(text, "bus", "addr_in")
    address = text[start:end]
    text = (text[:start] + address.replace("bus(addr_in)", "bus(raddr_in)", 1)
            + address.replace("bus(addr_in)", "bus(waddr_in)", 1) + text[end:])

    start, end = find_group(text, "pin", "ce_in")
    text = text[:start] + text[start:end].replace("pin(ce_in)", "pin(re_in)", 1) + text[end:]

    for group, port in (("rd_out", "raddr_in"), ("wd_in", "waddr_in"),
                        ("w_mask_in", "waddr_in")):
        start, end = find_group(text, "bus", group)
        body = text[start:end]
        if "address : addr_in;" not in body:
            raise SystemExit("ERROR: bus(%s) in %s has no memory address" % (group, path))
        text = text[:start] + body.replace("address : addr_in;",
                                           "address : %s;" % port) + text[end:]

    leftover = re.findall(r"\b(addr_in|ce_in)\b", text)
    if leftover:
        raise SystemExit("ERROR: %s still refers to %s after the two-port rewrite"
                         % (path, ", ".join(sorted(set(leftover)))))

    areas = re.findall(r"^(\s*area\s*:\s*)([\d.]+)(\s*;)", text, re.M)
    if len(areas) != 1:
        raise SystemExit("ERROR: expected one cell area in %s, found %d"
                         % (path, len(areas)))
    area = float(areas[0][1]) * area_factor
    text = re.sub(r"^(\s*area\s*:\s*)[\d.]+(\s*;)",
                  lambda m: "%s%.3f%s" % (m.group(1), area, m.group(2)), text,
                  count=1, flags=re.M)
    open(path, "w", encoding="utf-8").write(text)
    return area


def blackbox_verilog(spec, macros) -> str:
    """Port declarations only, for the Yosys/slang read of the design.

    The design instantiates these macros from the USE_FAKERAM branch of
    hdl/verilog/Sram.v, so slang needs a declaration; Yosys then marks them
    black boxes and their Liberty carries their timing and area.
    """
    out = [
        "// Black-box declarations of the FakeRAM2.0 macros this configuration",
        "// needs. Generated by flow/scripts/gen_sram_models.py -- do not edit.",
        "// Timing and area come from the matching Liberty in lib/.",
        "",
    ]
    for m in macros:
        if m.get("ports", "1rw") == "1rw":
            control = [
                "    input                ce_in,",
                "    input                we_in,",
                "    input  [%3d:0]       addr_in," % (m["addr_bits"] - 1),
            ]
        else:
            control = [
                "    input                re_in,",
                "    input  [%3d:0]       raddr_in," % (m["addr_bits"] - 1),
                "    input                we_in,",
                "    input  [%3d:0]       waddr_in," % (m["addr_bits"] - 1),
            ]
        out += (["module %s (" % m["name"], "    input                clk,"]
                + control + [
            "    input  [%3d:0]       wd_in," % (m["width"] - 1),
            "    input  [%3d:0]       w_mask_in," % (m["width"] - 1),
            "    output [%3d:0]       rd_out" % (m["width"] - 1),
            ");",
            "endmodule",
            "",
        ])
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="sram_spec.py output")
    ap.add_argument("--process", required=True, help="PDK FakeRAM process JSON")
    ap.add_argument("--fakeram-dir", required=True, help="third_party/FakeRAM2.0")
    ap.add_argument("--out-dir", required=True, help="where to write the models")
    ap.add_argument("--area-factor-2p", type=float, default=1.0,
                    help="area of a 1R1W macro relative to the 1RW macro of the "
                         "same shape (default: %(default)s)")
    args = ap.parse_args()

    spec = json.load(open(args.spec, encoding="utf-8"))
    process_json = json.load(open(args.process, encoding="utf-8"))
    process_json.pop("_comment", None)
    fr = load_fakeram(args.fakeram_dir)

    out = args.out_dir
    work = os.path.join(out, "fakeram")
    lib_dir = os.path.join(out, "lib")
    lef_dir = os.path.join(out, "lef")
    for d in (work, lib_dir, lef_dir):
        os.makedirs(d, exist_ok=True)
    # The macro set follows the RTL, so stale models from an earlier
    # configuration must not survive into this run's library list.
    for d in (lib_dir, lef_dir):
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))

    report = {"process": args.process, "area_factor_2p": args.area_factor_2p,
              "macros": []}
    for macro in spec["macros"]:
        ports = macro.get("ports", "1rw")
        banks, cmux, fits = choose_shape(fr, process_json, macro)
        process = fr["Process"](dict(process_json, column_mux_factor=cmux))
        sram_data = {"name": macro["name"], "width": macro["width"],
                     "depth": macro["depth"], "banks": banks}
        mem = fr["Memory"](process, sram_data, output_dir=work)
        apply_overrides(mem, process_json)

        fr["create_lib"](mem)
        fr["create_verilog"](mem)
        lef_written = False
        if fits and ports == "1rw":
            try:
                fr["create_lef"](mem)
                lef_written = True
            except SystemExit:
                lef_written = False
        if ports != "1rw":
            print("  note: %s is a two-port macro derived from a 1RW one; no LEF "
                  "written. Synthesis and STA only need the Liberty." % macro["name"],
                  file=sys.stderr)
        elif not lef_written:
            print("  note: %s is too short for its own pins; no LEF written. "
                  "Synthesis and STA only need the Liberty." % macro["name"],
                  file=sys.stderr)

        src_lib = os.path.join(mem.results_dir, macro["name"] + ".lib")
        patch_liberty(src_lib, mem)
        area_1rw = mem.area_um2
        area_factor = 1.0
        area = area_1rw
        if ports == "1r1w":
            area_factor = args.area_factor_2p
            area = to_two_port(src_lib, area_factor)
        shutil.copyfile(src_lib, os.path.join(lib_dir, macro["name"] + ".lib"))
        src_lef = os.path.join(mem.results_dir, macro["name"] + ".lef")
        if lef_written and os.path.isfile(src_lef):
            shutil.copyfile(src_lef, os.path.join(lef_dir, macro["name"] + ".lef"))

        report["macros"].append({
            "name": macro["name"], "ports": ports,
            "depth": macro["depth"], "width": macro["width"],
            "banks": banks, "column_mux_factor": cmux,
            "instances": macro["instances"],
            "area_um2": round(area, 4),
            "area_um2_1rw": round(area_1rw, 4), "area_factor": area_factor,
            "width_um": round(mem.width_um, 4), "height_um": round(mem.height_um, 4),
            "access_time_ns": mem.access_time_ns, "cycle_time_ns": mem.cycle_time_ns,
            "setup_ns": mem.t_setup_ns, "hold_ns": mem.t_hold_ns,
            "bits_per_um2": round(macro["depth"] * macro["width"] / area, 2),
            "lef": lef_written,
        })
        print("  %-20s %-4s %5dx%-4d banks=%d cmux=%d  %8.1f um^2 (%.1f x %.1f)  x%d"
              % (macro["name"], ports, macro["depth"], macro["width"], banks, cmux,
                 area, mem.width_um, mem.height_um, macro["instances"]))

    report["total_macro_area_um2"] = round(
        sum(m["area_um2"] * m["instances"] for m in report["macros"]), 4)

    with open(os.path.join(out, "sram.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
        fh.write("\n")
    with open(os.path.join(out, "sram_blackbox.v"), "w", encoding="utf-8") as fh:
        fh.write(blackbox_verilog(spec, spec["macros"]))
    print("  total macro area: %.1f um^2" % report["total_macro_area_um2"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
