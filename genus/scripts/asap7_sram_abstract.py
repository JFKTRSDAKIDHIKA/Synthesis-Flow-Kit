"""Planning-only N6 LEFs for ASAP7-based SRAM estimates, not SRAM IP layouts."""
import hashlib
import math
from pathlib import Path
import re


def group(text, kind, name):
    match = re.search(r"(?ms)^" + kind + r"\s+" + re.escape(name) + r"\s*\n.*?^END\s+" + re.escape(name) + r"\s*$", text)
    if not match:
        raise ValueError(f"Missing {kind} {name}")
    return match[0]


def number(text, name):
    return float(re.search(r"\b" + name + r"\s+([0-9.]+)", text)[1])


def native_geometry(path):
    text = Path(path).read_text()
    site = re.search(r"SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)", group(text, "SITE", "core"))
    layers = {}
    for name in ["M4", "M8"]:
        block = group(text, "LAYER", name)
        layers[name] = {key.lower(): number(block, key) for key in ["WIDTH", "PITCH", "AREA"]}
    return {"tech_lef": str(Path(path).resolve()),
            "tech_lef_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "manufacturing_grid_um": number(text, "MANUFACTURINGGRID"),
            "site_width_um": float(site[1]), "site_height_um": float(site[2]),
            "layers": layers,
            "scope": "N6 grid/layer envelope for synthetic planning abstracts; no native DRC, antenna, SRAM-internal or foundry qualification"}


def abstract(cfg, native):
    grid = native["manufacturing_grid_um"]
    snap = lambda value: round(round(value / grid) * grid, 6)
    up = lambda value, pitch: round(math.ceil((value - 1e-10) / pitch) * pitch, 6)
    layer = native["layers"]["M4"]
    pin_width = up(2 * layer["width"], grid)
    # Reserve four native tracks per signal pin. Pin geometry is a planning
    # choice; it is not taken from a characterized SRAM of this organization.
    pin_pitch = up(4 * layer["pitch"], grid)
    pin_length = up(max(8 * layer["pitch"], .04 / pin_width), grid)
    margin = 4 * layer["pitch"]
    width, depth = cfg["width"], cfg["depth"]
    aw = (depth - 1).bit_length()
    inputs = [("clk0", "CLOCK"), ("csb0", "SIGNAL")]
    inputs += [(f"addr0[{i}]", "SIGNAL") for i in range(aw)]
    inputs += [(f"din0[{i}]", "SIGNAL") for i in range(width)]
    if cfg["ports"] == "1rw":
        inputs += [("web0", "SIGNAL")]
        inputs += [(f"wmask0[{i}]", "SIGNAL") for i in range(width // 8)]
        outputs = [(f"dout0[{i}]", "SIGNAL") for i in range(width)]
    else:
        inputs += [("clk1", "CLOCK"), ("csb1", "SIGNAL")]
        inputs += [(f"addr1[{i}]", "SIGNAL") for i in range(aw)]
        outputs = [(f"dout1[{i}]", "SIGNAL") for i in range(width)]
    estimated_area = cfg["area_um2"]
    h = up(max(math.sqrt(estimated_area), 2 * margin + (max(len(inputs), len(outputs)) - 1) * pin_pitch + pin_width), native["site_height_um"])
    w = up(max(estimated_area / h, 2 * (pin_length + margin)), native["site_width_um"])
    cfg.update(area_reference_estimate_um2=estimated_area, area_um2=round(w * h, 9),
               width_um=w, height_um=h)
    pins = []
    for side, items, direction in [("left", inputs, "INPUT"), ("right", outputs, "OUTPUT")]:
        for index, (name, use) in enumerate(items):
            cy = margin + index * pin_pitch
            x1, x2 = (0, pin_length) if side == "left" else (w - pin_length, w)
            rect = [snap(x1), snap(cy - pin_width / 2), snap(x2), snap(cy + pin_width / 2)]
            pins.append(dict(name=name, direction=direction, use=use, layer="M4", rect=rect))
    pg_width = max(2 * native["layers"]["M8"]["width"], pin_width)
    for name, use, y in [("VDD", "POWER", h - 2 * margin), ("VSS", "GROUND", 2 * margin)]:
        pins.append(dict(name=name, direction="INOUT", use=use, layer="M8",
                         rect=[snap(margin), snap(y - pg_width / 2), snap(w - margin), snap(y + pg_width / 2)]))
    lines = ["# Synthetic SRAM planning abstract; ASAP7 area estimate, N6 layer/grid envelope.",
             "# No transistor layout, antenna characterization, or foundry SRAM validity.",
             "VERSION 5.8 ;", 'BUSBITCHARS "[]" ;', 'DIVIDERCHAR "/" ;',
             f"MACRO {cfg['name']}", "  CLASS BLOCK ;", "  ORIGIN 0 0 ;",
             f"  SIZE {w:.6f} BY {h:.6f} ;", "  SYMMETRY X Y ;"]
    for pin in pins:
        coords = " ".join(f"{x:.6f}" for x in pin["rect"])
        lines += [f"  PIN {pin['name']}", f"    DIRECTION {pin['direction']} ;", f"    USE {pin['use']} ;",
                  "    PORT", f"      LAYER {pin['layer']} ;", f"        RECT {coords} ;", "    END", f"  END {pin['name']}"]
    lines.append("  OBS")
    obstructions = []
    for name in ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7"]:
        rect = [snap(pin_length + margin), 0, snap(w - pin_length - margin), h] if name == "M4" else [0, 0, w, h]
        obstructions.append(dict(layer=name, rect=rect))
        lines += [f"    LAYER {name} ;", "      RECT " + " ".join(f"{x:.6f}" for x in rect) + " ;"]
    lines += ["  END", f"END {cfg['name']}", "END LIBRARY"]
    return "\n".join(lines) + "\n", {"name": cfg["name"], "width_um": w, "height_um": h,
        "pins": pins, "obstructions": obstructions, "pin_width_um": pin_width,
        "pin_pitch_um": pin_pitch, "pin_length_um": pin_length,
        "assumptions": ["Aspect ratio starts at1:1 and grows to fit pins; no floorplan coordinates specified",
                        "Signal pins are synthetic M4 left/right pins; PG access is M8 top/bottom",
                        "M0-M7 interior obstruction is conservative; internal SRAM routing is unknown",
                        "Mirroring is allowed for this abstract only; real IP orientation rules must replace it"]}
