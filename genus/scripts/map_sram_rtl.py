#!/usr/bin/env python3
"""Write the RTL synthesis reads: the emitted RTL with firtool memories on macros.

sram_spec.py lists, under `memories`, the firtool-emitted SyncReadMem modules
that become FakeRAM macros. The emitted RTL defines each one as a behavioural
array; this replaces that definition, header kept verbatim, with a body that
instantiates the macros side by side, so every instance in the design binds
to the macros without any change to the RTL generator. Nothing else in the
file is touched, and a configuration with no such memory gets a byte-identical
copy.

Port mapping, per slice:

  1RW   RW0_clk -> clk, RW0_en -> ce_in, RW0_wmode -> we_in,
        RW0_addr -> addr_in, RW0_wdata -> wd_in, RW0_rdata -> rd_out
  1R1W  R0_clk -> clk, R0_en -> re_in, R0_addr -> raddr_in, R0_data -> rd_out,
        W0_en -> we_in, W0_addr -> waddr_in, W0_data -> wd_in

The macros take a per-bit write mask, so a firtool mask of g-bit granules is
replicated g times per bit, and a memory without a mask writes all bits.
W0_clk is left unconnected: sram_spec.py only maps a 1R1W memory whose two
ports share one clock at every instance.

Read data outside the cycle after an enabled read is unspecified in the
firtool model (it drives 'bx), so the macro's own value there is a valid
refinement of it.

    map_sram_rtl.py --rtl <top.sv> --spec <sram.spec.json> --out <mapped.sv>
"""

from __future__ import annotations

import argparse
import json
import re
import sys


def mask_lines(mem: dict, mask_port: str) -> list[str]:
    width = mem["width"]
    if not mem["mask_bits"]:
        return ["  wire [%d:0] bitmask = {%d{1'b1}};" % (width - 1, width)]
    granule = width // mem["mask_bits"]
    return [
        "  wire [%d:0] bitmask;" % (width - 1),
        "  genvar i_mask;",
        "  generate",
        "    for (i_mask = 0; i_mask < %d; i_mask = i_mask + 1) begin : gen_bitmask"
        % mem["mask_bits"],
        "      assign bitmask[%d*i_mask +: %d] = {%d{%s[i_mask]}};"
        % (granule, granule, granule, mask_port),
        "    end",
        "  endgenerate",
    ]


def slice_lines(mem: dict) -> list[str]:
    out = []
    w = mem["slice_width"]
    for s in range(mem["slices"]):
        bits = "[%d:%d]" % (w * s + w - 1, w * s)
        if mem["ports"] == "1rw":
            pins = [("clk", "RW0_clk"), ("ce_in", "RW0_en"), ("we_in", "RW0_wmode"),
                    ("addr_in", "RW0_addr"), ("wd_in", "RW0_wdata" + bits),
                    ("w_mask_in", "bitmask" + bits), ("rd_out", "RW0_rdata" + bits)]
        else:
            pins = [("clk", "R0_clk"), ("re_in", "R0_en"), ("raddr_in", "R0_addr"),
                    ("rd_out", "R0_data" + bits), ("we_in", "W0_en"),
                    ("waddr_in", "W0_addr"), ("wd_in", "W0_data" + bits),
                    ("w_mask_in", "bitmask" + bits)]
        out.append("  %s u_slice_%d (" % (mem["macro"], s))
        out += ["    .%-9s (%s)%s" % (p, n, "," if i < len(pins) - 1 else "")
                for i, (p, n) in enumerate(pins)]
        out.append("  );")
    return out


def wrapper(header: str, mem: dict) -> str:
    mask_port = "RW0_wmask" if mem["ports"] == "1rw" else "W0_mask"
    body = [
        header,
        "  // flow/scripts/map_sram_rtl.py: firtool's behavioural %dx%d %s array,"
        % (mem["depth"], mem["width"], mem["ports"].upper()),
        "  // replaced by %d x %s (FakeRAM2.0) side by side." % (mem["slices"], mem["macro"]),
    ]
    body += mask_lines(mem, mask_port)
    body += slice_lines(mem)
    body.append("endmodule")
    return "\n".join(body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtl", required=True, help="emitted SystemVerilog")
    ap.add_argument("--spec", required=True, help="sram_spec.py output")
    ap.add_argument("--out", required=True, help="where to write the mapped RTL")
    args = ap.parse_args()

    text = open(args.rtl, encoding="utf-8", errors="replace").read()
    spec = json.load(open(args.spec, encoding="utf-8"))

    for mem in spec["memories"]:
        heads = list(re.finditer(r"^module\s+%s\s*\(" % re.escape(mem["module"]),
                                 text, re.M))
        if len(heads) != 1:
            raise SystemExit("ERROR: %d definitions of %s in %s; expected one"
                             % (len(heads), mem["module"], args.rtl))
        start = heads[0].start()
        header_end = text.index(");", start) + 2
        end = text.index("\nendmodule", start) + len("\nendmodule")
        text = text[:start] + wrapper(text[start:header_end], mem) + text[end:]

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    for mem in spec["memories"]:
        print("  %-26s -> %d x %s" % (mem["module"], mem["slices"], mem["macro"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
