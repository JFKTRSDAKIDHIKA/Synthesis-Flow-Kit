#!/usr/bin/env python3
"""Replay functional and hierarchy checks; does not run synthesis or PnR."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', required=True, type=Path)
    parser.add_argument('--reference-binding', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--yosys', default='yosys', help='Yosys with the Slang plugin')
    parser.add_argument('--iverilog', default='iverilog')
    parser.add_argument('--vvp', default='vvp')
    a = parser.parse_args()
    binding, reference, out = a.binding.resolve(), a.reference_binding.resolve(), a.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    tb = Path(__file__).resolve().parents[1]/'tests/ameooo_wide_sram_tb.sv'
    # Frozen copies may place the testbench alongside the replay program.
    if not tb.exists():
        tb = Path(__file__).resolve().with_name('ameooo_wide_sram_tb.sv')
    shutil.copyfile(tb,out/tb.name)
    files = [out/tb.name,binding/'wrappers.sv',binding/'reference-memories.sv',
             *sorted((binding/'models').glob('*.v')),reference/'models/asap7_est_mrf_1024x64_1r1w.v']
    inputs = list(dict.fromkeys(files+[binding/'AMEOOO.asap7-est.sv',binding/'blackboxes.v',binding/'binding-manifest.json']))
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    locks = {str(p):sha(p) for p in inputs}
    commands = []
    def run(cmd, name, expected=0):
        commands.append({'command':cmd,'log':name})
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        with (out/name).open('w') as f:
            p = subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
        assert (p.returncode == 0) if expected == 0 else (p.returncode != 0), (name,p.returncode)
        return (out/name).read_text()
    def compile_cmd(paths, output):
        return [a.iverilog,'-g2012','-DSYNTHESIS','-s','ameooo_wide_sram_tb','-o',str(output),*map(str,paths)]
    run(compile_cmd(files,out/'positive.vvp'),'compile.log')
    log = run([a.vvp,str(out/'positive.vvp')],'simulation.log')
    match = re.search(r'AMEOOO_WIDE_SRAM_PASS bit_comparisons=(\d+) writes=(\d+) independent_reads=(\d+) padding_checks=(\d+)',log)
    assert match and int(match[4]) == 256
    text = (binding/'wrappers.sv').read_text()
    needle = '.addr0(W0_addr), .din0(W0_data),'
    assert text.count(needle) == 1
    bad = out/'negative-lane-rotation.sv'
    bad.write_text(text.replace(needle,'.addr0(W0_addr), .din0({W0_data[191:0], W0_data[255:192]}),'))
    paths = [bad if p == binding/'wrappers.sv' else p for p in files]
    run(compile_cmd(paths,out/'negative.vvp'),'negative-compile.log')
    badlog = run([a.vvp,str(out/'negative.vvp')],'negative-simulation.log',expected=1)
    assert 'MRF0 mismatch' in badlog
    ys = out/'hierarchy.ys'
    ys.write_text('\n'.join([
        f'read_slang --best-effort-hierarchy --empty-blackboxes --top AMEOOO -DSYNTHESIS {binding}/AMEOOO.asap7-est.sv {binding}/blackboxes.v',
        'hierarchy -check -top AMEOOO',f'tee -o {out}/hierarchy-stat.json stat -json'])+'\n')
    run([a.yosys,'-m','slang','-Q','-T',str(ys)],'hierarchy.log')
    counts = json.loads((out/'hierarchy-stat.json').read_text())['design']['num_cells_by_type']
    expected = {'asap7_est_itcm_128x256_1rw_bytewrite':2,'asap7_est_dtcm_2048x256_1rw_bytewrite':2,'asap7_est_mrf_1024x256_1r1w':16}
    assert {k:v for k,v in counts.items() if 'asap7_est_' in k} == expected
    assert all(sha(Path(p)) == digest for p,digest in locks.items())
    result = {'status':'passed','scope':'Functional comparison and independent full-RTL hierarchy; no synthesis timing or physical qualification',
              'bit_comparisons':int(match[1]),'writes':int(match[2]),'independent_reads':int(match[3]),'padding_checks':int(match[4]),
              'negative_lane_rotation':'rejected','macro_counts':expected,'input_sha256':locks,
              'program_sha256':sha(Path(__file__))}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='input_sha256'}),flush=True)


if __name__ == '__main__':
    main()
