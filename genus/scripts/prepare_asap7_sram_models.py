#!/usr/bin/env python3
"""Generate explicitly estimated SRAM boundary models from public ASAP7 SRAMs.

This is a parameter/model generator, not an OpenRAM ASAP7 technology port.
It neither invokes synthesis nor generates transistor-level layout. Source
Liberty tables retain the ASAP7 TT 0.7 V/25 C numerical reference. An optional
model voltage changes voltage metadata only, without recharacterization or
timing/power scaling. The byte-mask and 1R1W interfaces are assumptions.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from prepare_asap7 import group_end
from asap7_sram_abstract import native_geometry, abstract

NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def group(text, kind, name):
    match = re.search(r"\b" + kind + r"\s*\(\s*" + re.escape(name) + r"\s*\)\s*\{", text)
    if not match:
        raise ValueError(f"Missing {kind}({name})")
    return text[match.start():group_end(text, text.index("{", match.start()))]


def timing_values(text, timing_type):
    result = []
    for match in re.finditer(r"\btiming\s*\([^)]*\)\s*\{", text):
        body = text[match.start():group_end(text, text.index("{", match.start()))]
        if not re.search(r"\btiming_type\s*:\s*" + timing_type + r"\s*;", body):
            continue
        if timing_type == "rising_edge":
            for kind in ["cell_rise", "cell_fall"]:
                result.extend(table_values(body, kind))
            continue
        for value in re.finditer(r"\bvalues\s*\((.*?)\)\s*;", body, re.S):
            result.extend(float(x) for x in re.findall(NUMBER, value[1]))
    if not result:
        raise ValueError("Missing timing values: " + timing_type)
    return result


def table_values(text, name):
    body = group(text, name, "delay_template_7x7_x1")
    values = re.search(r"\bvalues\s*\((.*?)\)\s*;", body, re.S)
    return [float(x) for x in re.findall(NUMBER, values[1])]


def attributes_without_power(text):
    """Retain source timing templates, but not unsupported power information."""
    start = re.search(r"\blibrary\s*\([^)]*\)\s*\{", text).end()
    end = re.search(r"\bcell\s*\([^)]*\)\s*\{", text).start()
    header = text[start:end]
    for match in reversed(list(re.finditer(r"\b(?:type|power_lut_template)\s*\([^)]*\)\s*\{", header))):
        stop = group_end(header, header.index("{", match.start()))
        header = header[:match.start()] + header[stop:]
    header = re.sub(r"(?m)^\s*(?:comment|date|revision|default_cell_leakage_power|default_leakage_power_density)\s*:.*?;", "", header)
    return header


def bus_type(name, width):
    return f"""  type ({name}) {{
    base_type : array; data_type : bit; bit_width : {width};
    bit_from : {width - 1}; bit_to : 0; downto : true;
  }}"""


def constraints(clock, setup, hold):
    result = []
    for kind, value in [("setup_rising", setup), ("hold_rising", hold)]:
        tables = "\n".join(f"""        {edge}_constraint (constraint_template_7x7_x1) {{
          index_1 (\"0.005, 0.32\"); index_2 (\"0.005, 0.32\");
          values (\"{value:.9g}, {value:.9g}\", \"{value:.9g}, {value:.9g}\");
        }}""" for edge in ["rise", "fall"])
        result.append(f"""      timing () {{
        related_pin : \"{clock}\"; timing_type : {kind};
{tables}
      }}""")
    return "\n".join(result)


def input_pin(name, clock, cap, setup, hold):
    return f"""    pin ({name}) {{
      direction : input; related_power_pin : VDD; related_ground_pin : VSS;
      capacitance : {cap:.9g}; max_transition : 0.32;
{constraints(clock, setup, hold)}
    }}"""


def input_bus(name, width, clock, cap, setup, hold):
    pins = "\n".join(f"      pin ({name}[{i}]) {{ capacitance : {cap:.9g}; max_transition : 0.32; related_power_pin : VDD; related_ground_pin : VSS; }}" for i in range(width))
    return f"""    bus ({name}) {{
      bus_type : T_{name}; direction : input;
{pins}
{constraints(clock, setup, hold)}
    }}"""


def output_timing(source, clock, delay_offset, delay_factor):
    bus = group(source, "bus", "dataout")
    match = re.search(r"\btiming\s*\([^)]*\)\s*\{", bus)
    timing = bus[match.start():group_end(bus, bus.index("{", match.start()))]
    timing = timing.replace('related_pin : "clk"', f'related_pin : "{clock}"')
    # Transform delay values only. Slew/load axes and output-transition tables
    # remain unchanged; each transformation is recorded in the manifest.
    for kind in ["cell_rise", "cell_fall"]:
        old = group(timing, kind, "delay_template_7x7_x1")
        def replace_values(match):
            values = re.sub(NUMBER, lambda n: f"{(float(n[0]) + delay_offset) * delay_factor:.9g}", match[1])
            return "values (" + values + ");"
        new = re.sub(r"values\s*\((.*?)\)\s*;", replace_values, old, flags=re.S)
        timing = timing.replace(old, new)
    return timing


def make_liberty(source, cfg, parameters):
    name, width, depth, ports = (cfg[k] for k in ["name", "width", "depth", "ports"])
    aw = (depth - 1).bit_length()
    setup, hold = parameters["setup_envelope_ns"], parameters["hold_envelope_ns"]
    cap = parameters["input_capacitance_pf"]
    read_clock = "clk1" if ports == "1r1w" else "clk0"
    header = attributes_without_power(source)
    model_voltage = parameters["model_voltage_v"]
    if model_voltage != parameters["reference_voltage_v"]:
        header = re.sub(r"(nom_voltage\s*:\s*)" + NUMBER, lambda m: m[1] + f"{model_voltage:g}", header)
        header = re.sub(r"(voltage_map\s*\(VDD,\s*)" + NUMBER, lambda m: m[1] + f"{model_voltage:g}", header)
        header = re.sub(r"(\bvoltage\s*:\s*)" + NUMBER, lambda m: m[1] + f"{model_voltage:g}", header)
        header = header.replace("PVT_0P7V_25C", "EXPLORATORY_" + f"{model_voltage:g}".replace(".", "P") + "V_25C")
    types = [bus_type("T_addr0", aw), bus_type("T_din0", width), bus_type("T_dout" + ("1" if ports == "1r1w" else "0"), width)]
    pins = []
    for clock in (["clk0", "clk1"] if ports == "1r1w" else ["clk0"]):
        original = group(source, "pin", "clk")
        # The source minimum_period tables contain zeros, so omit that
        # unsupported limit rather than claim a validated macro cycle time.
        for match in reversed(list(re.finditer(r"\btiming\s*\([^)]*\)\s*\{", original))):
            stop = group_end(original, original.index("{", match.start()))
            if "minimum_period" in original[match.start():stop]:
                original = original[:match.start()] + original[stop:]
        original = re.sub(r"pin\s*\(clk\)", "pin (" + clock + ")", original, count=1)
        original = original.replace('related_pin : "clk"', f'related_pin : "{clock}"')
        # Source internal-power tables do not characterize these derivatives.
        for match in reversed(list(re.finditer(r"\binternal_power\s*\([^)]*\)\s*\{", original))):
            stop = group_end(original, original.index("{", match.start()))
            original = original[:match.start()] + original[stop:]
        pins.append(original)
    pins += [input_pin("csb0", "clk0", cap, setup, hold), input_bus("addr0", aw, "clk0", cap, setup, hold), input_bus("din0", width, "clk0", cap, setup, hold)]
    if ports == "1rw":
        types.append(bus_type("T_wmask0", width // 8))
        pins += [input_pin("web0", "clk0", cap, setup, hold), input_bus("wmask0", width // 8, "clk0", cap, setup, hold)]
    else:
        types.append(bus_type("T_addr1", aw))
        pins += [input_pin("csb1", "clk1", cap, setup, hold), input_bus("addr1", aw, "clk1", cap, setup, hold)]
    dout = "dout1" if ports == "1r1w" else "dout0"
    output_pins = "\n".join(f"      pin ({dout}[{i}]) {{ related_power_pin : VDD; related_ground_pin : VSS; }}" for i in range(width))
    timing = output_timing(source, read_clock, cfg["delay_offset_ns"], cfg["delay_factor"])
    pins.append(f"""    bus ({dout}) {{
      bus_type : T_{dout}; direction : output;
      max_capacitance : 0.36864;
{output_pins}
{timing}
    }}""")
    pg = "\n".join(group(source, "pg_pin", n) for n in ["VDD", "VSS"])
    type_text = "\n".join(types)
    pin_text = "\n".join(pins)
    return f"""/* ASAP7-derived estimated boundary model; NOT OpenRAM or N6 SRAM IP.
 * Power and maximum operating frequency are not characterized.
 */
library ({name}) {{
/* Declared model voltage {model_voltage:g} V; numerical reference remains
 * ASAP7 0.7 V. Voltage metadata only; no recharacterization or delay scaling. */
{header}
{type_text}
  cell ({name}) {{
    area : {cfg['area_um2']:.9g}; dont_use : true; interface_timing : true;
{pg}
{pin_text}
  }}
}}
"""


def make_verilog(cfg, blackbox=False):
    width, depth, ports, name = (cfg[k] for k in ["width", "depth", "ports", "name"])
    aw = (depth - 1).bit_length()
    declarations = f"input clk0, csb0, input [{aw-1}:0] addr0, input [{width-1}:0] din0, "
    if ports == "1rw":
        declarations += f"input web0, input [{width//8-1}:0] wmask0, output reg [{width-1}:0] dout0"
        behavior = f"""integer lane;
  always @(posedge clk0) begin
    if (!csb0 && addr0 < {depth}) begin
      if (!web0) begin
        for (lane=0; lane<{width//8}; lane=lane+1)
          if (wmask0[lane]) mem[addr0][lane*8 +: 8] <= din0[lane*8 +: 8];
      end else dout0 <= mem[addr0];
    end
  end"""
    else:
        declarations += f"input clk1, csb1, input [{aw-1}:0] addr1, output reg [{width-1}:0] dout1"
        behavior = f"""// Independent clocks/addresses. Same-address concurrent read/write
  // behavior is not qualified; system integration must resolve collisions.
  always @(posedge clk0)
    if (!csb0 && addr0 < {depth}) mem[addr0] <= din0;
  always @(posedge clk1)
    if (!csb1 && addr1 < {depth}) dout1 <= mem[addr1];"""
    prefix = "(* blackbox *) " if blackbox else ""
    body = "" if blackbox else f"  reg [{width-1}:0] mem [0:{depth-1}];\n  {behavior}\n"
    return f"// ASAP7-derived functional boundary estimate, no reset/initialization.\n{prefix}module {name}({declarations});\n{body}endmodule\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asap7-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--mrf-width", type=int, choices=[64, 128, 256, 512], default=64,
                        help="Payload width per MRF macro; two 2048-bit read replicas are retained")
    parser.add_argument("--tcm-slice-width", type=int, choices=[128, 256, 512], default=128,
                        help="Payload width per TCM macro, with native byte-write enables")
    parser.add_argument("--model-voltage", type=float, default=0.7,
                        help="Exploratory voltage metadata only; timing/area/capacitance remain unchanged from the 0.7V reference")
    parser.add_argument("--n6-tech-lef", type=Path,
                        help="Optional native grid/layer input for planning-only N6 LEFs; no N6 SRAM timing is read")
    parser.add_argument("--dual-port-area-factor", type=float, default=2.0,
                        help="Explicit uncharacterized 1R1W area assumption; not an ASAP7 measurement")
    parser.add_argument("--dual-port-delay-factor", type=float, default=1.25,
                        help="Explicit uncharacterized 1R1W clock-to-output assumption")
    args = parser.parse_args()
    if not math.isfinite(args.model_voltage) or args.model_voltage <= 0:
        parser.error("Model voltage must be finite and positive")
    if args.dual_port_area_factor < 1 or args.dual_port_delay_factor < 1:
        parser.error("Dual-port estimate factors must be at least one")
    source_root = args.asap7_root.resolve()
    texts = {}
    for rows, width in [(64, 64), (128, 64), (256, 64), (256, 80)]:
        path = source_root / "generated/LIB" / f"srambank_{rows}x4x{width}_6t122.lib"
        texts[(rows * 4, width)] = (path, path.read_text())
    nominal = texts[(1024, 64)][1]
    assert 'time_unit : "1ns"' in nominal and 'capacitive_load_unit (1,pf)' in nominal
    assert re.search(r"nom_voltage\s*:\s*0.7\s*;", nominal)
    assert re.search(r"nom_temperature\s*:\s*25\s*;", nominal)
    parameters = {
        "time_unit": "ns", "capacitance_unit": "pF", "area_unit": "um^2",
        "reference_voltage_v": 0.7, "reference_temperature_c": 25,
        "model_voltage_v": args.model_voltage,
        "voltage_treatment": "metadata_only_no_recharacterization_no_numerical_scaling",
        "setup_envelope_ns": max(timing_values(nominal, "setup_rising")),
        "hold_envelope_ns": max(timing_values(nominal, "hold_rising")),
        "input_capacitance_pf": float(re.search(r"\bcapacitance\s*:\s*(" + NUMBER + ")", group(nominal, "pin", "banksel"))[1]),
        "clock_capacitance_pf": float(re.search(r"\bcapacitance\s*:\s*(" + NUMBER + ")", group(nominal, "pin", "clk"))[1]),
        "dual_port_area_factor_assumed": args.dual_port_area_factor,
        "dual_port_delay_factor_assumed": args.dual_port_delay_factor,
    }
    def delay_at(depth, width):
        return table_values(texts[(depth, width)][1], "cell_rise")[0]
    depth_slope = max(delay_at(512, 64) - delay_at(256, 64), delay_at(1024, 64) - delay_at(512, 64))
    width_slope = (delay_at(1024, 80) - delay_at(1024, 64)) / 16
    parameters.update(depth_delay_per_doubling_ns=depth_slope, width_delay_per_bit_ns=width_slope)
    configurations = [
        dict(name=f"asap7_est_itcm_128x{args.tcm_slice_width}_1rw_bytewrite", logical_module="memory_128x512", logical_depth=128, depth=128, width=args.tcm_slice_width, ports="1rw", instances=512 // args.tcm_slice_width, source_depth=256),
        dict(name=f"asap7_est_dtcm_2048x{args.tcm_slice_width}_1rw_bytewrite", logical_module="memory_2048x512", logical_depth=2048, depth=2048, width=args.tcm_slice_width, ports="1rw", instances=512 // args.tcm_slice_width, source_depth=1024),
        dict(name=f"asap7_est_mrf_1024x{args.mrf_width}_1r1w", logical_module="bankData_memoryForReadPort_768x64", logical_depth=768, depth=1024, width=args.mrf_width, ports="1r1w", instances=4096 // args.mrf_width, source_depth=1024),
    ]
    native = native_geometry(args.n6_tech_lef) if args.n6_tech_lef else None
    abstract_records = []
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for folder in ["lib", "models", "blackboxes"]:
        (output / folder).mkdir()
    if native:
        (output / "lef-n6-planning").mkdir()
    for cfg in configurations:
        path, source = texts[(cfg["source_depth"], 64)]
        source_area = float(re.search(r"\barea\s*:\s*(" + NUMBER + ")", source)[1])
        cfg["source_liberty"] = str(path)
        cfg["source_area_um2"] = source_area
        cfg["area_factor"] = args.dual_port_area_factor if cfg["ports"] == "1r1w" else 1.0
        cfg["area_um2"] = source_area * cfg["depth"] * cfg["width"] / (cfg["source_depth"] * 64) * cfg["area_factor"]
        cfg["padding_words_per_instance"] = cfg["depth"] - cfg["logical_depth"]
        if native:
            lef, record = abstract(cfg, native)
            abstract_records.append(record)
            (output / "lef-n6-planning" / (cfg["name"] + ".lef")).write_text(lef)
        # Do not reduce below the selected source's access time for smaller
        # arrays. Depth/width extrapolation is a planning assumption only.
        cfg["delay_offset_ns"] = max(0, math.log2(cfg["depth"] / cfg["source_depth"])) * depth_slope + max(0, cfg["width"] - 64) * width_slope
        cfg["delay_factor"] = args.dual_port_delay_factor if cfg["ports"] == "1r1w" else 1.0
        (output / "lib" / (cfg["name"] + ".lib")).write_text(make_liberty(source, cfg, parameters))
        (output / "models" / (cfg["name"] + ".v")).write_text(make_verilog(cfg))
        (output / "blackboxes" / (cfg["name"] + ".v")).write_text(make_verilog(cfg, True))
        cfg["clock_to_output_ns_range"] = [min(timing_values((output / "lib" / (cfg["name"] + ".lib")).read_text(), "rising_edge")), max(timing_values((output / "lib" / (cfg["name"] + ".lib")).read_text(), "rising_edge"))]
    shutil.copyfile(source_root / "LICENSE", output / "ASAP7-SRAM-LICENSE")
    if native:
        (output / "physical-abstracts.json").write_text(json.dumps({"native_grid": native, "macros": abstract_records}, indent=2) + "\n")
    (output / "generator").mkdir()
    for name in ["prepare_asap7_sram_models.py", "prepare_asap7.py", "asap7_sram_abstract.py"]:
        shutil.copyfile(Path(__file__).parent / name, output / "generator" / name)
    manifest = {
        "model_id": ("ameooo-asap7-derived-sram-estimates-wide-v5" if (args.mrf_width, args.tcm_slice_width) != (64, 128) else ("ameooo-asap7-derived-sram-estimates-padded-v3" if args.model_voltage == 0.7 else "ameooo-asap7-derived-sram-estimates-padded-v4")), "status": "models_generated_not_synthesized",
        "organization": {"mrf_width": args.mrf_width, "tcm_slice_width": args.tcm_slice_width,
                         "mrf_read_replicas": 2, "mrf_bits_per_replica_word": 2048},
        "generator_sha256": sha(__file__), "source_repository": "https://github.com/The-OpenROAD-Project/asap7_sram_0p0",
        "source_commit": subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True).strip(),
        "input_sha256": {str(path): sha(path) for path, _ in texts.values()},
        "parameters": parameters, "macros": configurations,
        "logical_bytes": sum(c["logical_depth"] * c["width"] * c["instances"] // 8 for c in configurations),
        "physical_model_bytes": sum(c["depth"] * c["width"] * c["instances"] // 8 for c in configurations),
        "unused_padding_bytes": sum((c["depth"]-c["logical_depth"]) * c["width"] * c["instances"] // 8 for c in configurations),
        "macro_instances": sum(c["instances"] for c in configurations),
        "physical_abstracts": native or "not requested",
        "catalog_basis": "Power-of-two model menu with native byte masks and1R1W functionality; IHP/SKY130 organization examples. These exact wide/masked ASAP7 macros are derived estimates, not delivered IP.",
        "assumptions": [
            "No FreePDK45 model, parameter, timing table or layout is used.",
            f"ASAP7 TT0.7V/25C numerical data is retained; declared model supply is {args.model_voltage:g}V. Voltage metadata only, no recharacterization or feature-size/timing/power scaling.",
            "Area scales selected published macro area linearly with bit capacity; this does not model decoder/periphery scaling accurately.",
            "Clock-to-output tables retain source slew/load axes; size extrapolation adds source-derived nonnegative depth/width offsets.",
            "Setup/hold are flat worst-value envelopes of the source's input tables, including byte-mask pins treated like write data.",
            "1R1W area and access multipliers are explicit uncharacterized assumptions, not a measured 1R1W ASAP7 macro.",
            "No additional externally visible cycle or serial read-modify-write is introduced for byte writes.",
            "MRF has1024physical words and768usable words; each replica reserves256unused words, fully included in area/capacity.",
            "Logical byte totals include existing MRF read-port replicas; they are not unique architectural storage capacity.",
            "When planning LEFs are requested, Liberty area equals the snapped abstract bounding-box area; unsnapped source-based area is recorded separately.",
        ],
        "unqualified": ["Native N6 SRAM", "OpenRAM ASAP7 technology port", "Transistor-level implementation/GDS and native qualified LEF", "Power and leakage", "Minimum cycle time (source tables are zero)", "1R1W characterization and read/write collision contract", "Native routing/pin-access/antenna and physical qualification of synthetic planning LEFs", "MMMC/IR/EM/DRC/LVS signoff", "Binding to newly emitted785c6cba RTL (emission paused)"],
        "output_sha256": {str(p.relative_to(output)): sha(p) for p in output.rglob("*") if p.is_file()},
    }
    (output / "model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(output), "parameters": parameters, "macros": configurations}, indent=2))


if __name__ == "__main__":
    main()
