#!/usr/bin/env python3
"""Bind emitted AMEOOO memory definitions to the reviewed ASAP7 estimates.

Requires freshly emitted campaign RTL and sram_spec.py output. This command
prepares inputs only; it never launches synthesis. Non-memory RTL is unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def definition(text, name):
    matches = list(re.finditer(r"^module\s+" + re.escape(name) + r"\s*\(", text, re.M))
    if len(matches) != 1:
        raise ValueError("Expected one original memory definition: " + name)
    start = matches[0].start()
    header_end = text.index(");", start) + 2
    end = text.index("\nendmodule", header_end) + len("\nendmodule")
    return start, header_end, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--models", required=True, type=Path)
    args = parser.parse_args()
    campaign, models = args.campaign.resolve(), args.models.resolve()
    selection_path = campaign / "sram-model-selection.json"
    if selection_path.is_file():
        selection = json.loads(selection_path.read_text())
        selected = Path(selection["selected_sram_model_manifest"]).resolve()
        if selected != (models / "model-manifest.json").resolve():
            raise ValueError("Requested SRAM models differ from this campaign's selected manifest")
    identity = json.loads((models / "model-manifest.json").read_text())
    if identity["model_id"] not in ["ameooo-asap7-derived-sram-estimates-padded-v3", "ameooo-asap7-derived-sram-estimates-padded-v4"]:
        raise ValueError("Physical SRAM catalog/model revision required before binding: use the padded-v3 model set")
    model_voltage = identity["parameters"].get("model_voltage_v", identity["parameters"]["reference_voltage_v"])
    if selection_path.is_file() and "target_sram_supply_v" in selection:
        if selection["target_sram_supply_v"] != model_voltage:
            raise ValueError("Selected SRAM model voltage differs from campaign target supply")
    for relative, value in identity["output_sha256"].items():
        assert sha(models / relative) == value, relative
    expected = {
        "memory_128x512": (128, 512, "1rw", 64, 1),
        "memory_2048x512": (2048, 512, "1rw", 64, 1),
        "bankData_memoryForReadPort_768x64": (768, 64, "1r1w", 0, 64),
    }
    spec_path = campaign / "sram-spec.json"
    spec = json.loads(spec_path.read_text())
    actual = {m["module"]: tuple(m[k] for k in ["depth", "width", "ports", "mask_bits", "instances"]) for m in spec["memories"]}
    if actual != expected:
        raise ValueError("Fresh memory identity differs; revise models explicitly: " + repr(actual))
    source_path = campaign / "rtl/AMEOOO.sv"
    original = source_path.read_text()
    source_hash = sha(source_path)
    replacements, references, wrappers, macros = [], [], [], []
    modules = list(expected)
    configs = {cfg["logical_module"]: cfg for cfg in identity["macros"]}
    assert set(configs) == set(modules)
    assert [(configs[m]["depth"], configs[m]["width"], configs[m]["ports"], configs[m]["instances"]) for m in modules] == [(128,128,"1rw",4),(2048,128,"1rw",4),(1024,64,"1r1w",64)]
    assert all(configs[m]["logical_depth"] == expected[m][0] for m in modules)
    for module in modules:
        cfg = configs[module]
        start, header_end, end = definition(original, module)
        reference = original[start:end].replace("module " + module + "(", "module ref_" + module + "(", 1)
        references.append(reference)
        lines = [original[start:header_end], "  // ASAP7-based boundary estimate; no N6 SRAM characterization."]
        if cfg["ports"] == "1rw":
            for i in range(4):
                lines += [f"  {cfg['name']} u_slice_{i} (",
                          "    .clk0(RW0_clk), .csb0(~RW0_en), .web0(~RW0_wmode),",
                          f"    .wmask0(RW0_wmask[{16*i} +: 16]), .addr0(RW0_addr),",
                          f"    .din0(RW0_wdata[{128*i} +: 128]), .dout0(RW0_rdata[{128*i} +: 128])", "  );"]
        else:
            lines += [f"  {cfg['name']} u_memory (",
                      "    .clk0(W0_clk), .csb0(~(W0_en && (W0_addr < 10'd768))),",
                      "    .addr0(W0_addr), .din0(W0_data),",
                      "    .clk1(R0_clk), .csb1(~(R0_en && (R0_addr < 10'd768))),",
                      "    .addr1(R0_addr), .dout1(R0_data)", "  );"]
        lines.append("endmodule")
        wrapper = "\n".join(lines)
        wrappers.append(wrapper)
        replacements.append((start, end, wrapper))
        macros.append({k: cfg[k] for k in ["name", "ports", "depth", "width", "instances"]})
        macros[-1].update(addr_bits=(cfg["depth"]-1).bit_length(), bits=cfg["depth"] * cfg["width"])
    mapped = original
    # Reconstructing all untouched segments proves no other emitted RTL text
    # was altered. Source RTL itself is only read, never modified.
    for start, end, wrapper in sorted(replacements, reverse=True):
        mapped = mapped[:start] + wrapper + mapped[end:]
    out = campaign / "memory-binding-asap7"
    out.mkdir(exist_ok=False)
    for folder in ["models", "lib"]:
        shutil.copytree(models / folder, out / folder)
    (out / "blackboxes.v").write_text("\n".join((models / "blackboxes" / (m["name"] + ".v")).read_text() for m in macros))
    (out / "wrappers.sv").write_text("\n\n".join(wrappers) + "\n")
    (out / "reference-memories.sv").write_text("\n\n".join(references) + "\n")
    (out / "AMEOOO.asap7-est.sv").write_text(mapped)
    (out / "macro-spec.json").write_text(json.dumps({"top": "AMEOOO", "macros": macros,
        "logical_macro_bits": spec["total_bits"], "physical_macro_bits": identity["physical_model_bytes"] * 8,
        "unused_macro_bits": identity["physical_model_bytes"] * 8 - spec["total_bits"]}, indent=2) + "\n")
    assert sha(source_path) == source_hash
    evidence = {"model_manifest": str(models / "model-manifest.json"), "model_manifest_sha256": sha(models / "model-manifest.json"),
        "input_sha256": {str(source_path): source_hash, str(spec_path): sha(spec_path)},
        "changed_modules_only": modules, "source_rtl_unchanged": True,
        "scope": f"Exploratory SRAM declared at {model_voltage:g}V; numerical reference ASAP7 TT0.7V, no voltage recharacterization; not native N6 SRAM",
        "fresh_interface_verification": "required before synthesis", "synthesis_launched": False,
        "generated_sha256": {str(p.relative_to(out)): sha(p) for p in out.rglob("*") if p.is_file()}}
    (out / "binding-manifest.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
