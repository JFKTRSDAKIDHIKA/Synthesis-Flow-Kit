import os
import tempfile
import unittest
from pathlib import Path
from genus_yosys_plan import prepare
from genus_blocks import build_plan

class YosysBoundaryTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('YOSYS'), 'requires workspace Yosys/slang')
    def test_tied_instances_reuse_exact_yosys_specialization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); rtl = root/'test.sv'; bb = root/'bb.v'; bb.write_text('')
            rtl.write_text("module Fma(input clk_i, input [3:0] a,b, output reg [3:0] y); always @(posedge clk_i) y<=a+b; endmodule\n"
                "module Top(input io_aclk, input [3:0] b, output [3:0] y,z,w);\n"
                " Fma u(.clk_i(io_aclk),.a(4'h3),.b(b),.y(y));\n"
                " Fma v(.clk_i(io_aclk),.a(4'h3),.b(b),.y(z));\n"
                " Fma q(.clk_i(io_aclk),.a(4'h1),.b(b),.y(w));\nendmodule\n")
            data, blocks, original = prepare(rtl, bb, 'Top', ['Fma'], 'SYNTHESIS', [os.environ['YOSYS']], root/'plan')
            mapped = build_plan(data, 'Top', blocks)
            self.assertEqual(len(mapped['units']), 3)
            self.assertEqual(sorted(len(s['sites']) for s in original['blocks'][0]['specializations']), [1, 2])
            self.assertTrue(all(u['clock'] == 'clk_i' for u in mapped['units'] if not u['top']))
            self.assertEqual(len(mapped['units'][-1]['children']), 2)
