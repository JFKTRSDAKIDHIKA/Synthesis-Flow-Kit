#!/usr/bin/env python3
"""Run licensed Genus, normalize its mapped netlist, and check macro counts."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
from collections import Counter
from netlist_lib import parse_modules, read_liberty
from iteration import load_customization, environment, validate_checkpoint, seal_checkpoints, checkpoint_identity, CHECKPOINTS


def yosys_quote(path):
    return json.dumps(str(path))


def validate_macros(netlist, top, spec, libraries):
    modules = parse_modules(netlist.read_text().splitlines())
    mods = {m.name: m for m in modules}
    if len(mods) != len(modules) or top not in mods:
        raise RuntimeError('Duplicate modules or missing top in normalized netlist')
    cells = read_liberty(libraries)
    counts = Counter()
    def visit(name, active):
        if name in active:
            raise RuntimeError('Recursive module hierarchy: ' + name)
        if name in cells:
            counts[name] += 1
            return
        if name not in mods:
            raise RuntimeError('Unresolved cell/module: ' + name)
        for inst in mods[name].instances:
            visit(inst.type, active | {name})
    visit(top, set())
    for macro in json.loads(spec.read_text())['macros']:
        if counts[macro['name']] != macro['instances']:
            raise RuntimeError('Macro count mismatch for %s: expected %d, found %d' %
                               (macro['name'], macro['instances'], counts[macro['name']]))
    return dict(counts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--genus', default='genus')
    ap.add_argument('--yosys', default='yosys')
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--out-dir', type=Path, required=True)
    for name in ['rtl', 'blackboxes', 'std-lib', 'sram-lib-dir', 'spec', 'netlist', 'script']:
        ap.add_argument('--' + name, type=Path)
    ap.add_argument('--top')
    ap.add_argument('--clk-port', default='io_aclk')
    ap.add_argument('--period-ps', type=int, default=2500)
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--hierarchy', choices=['preserve', 'flat'], default='preserve')
    ap.add_argument('--child-netlist', type=Path, action='append', default=[])
    ap.add_argument('--preserve-block', action='append', default=[])
    ap.add_argument('--unit-mode', action='store_true')
    ap.add_argument('--effort', choices=['low','medium','high'], default='medium')
    ap.add_argument('--defines', default='SYNTHESIS USE_FAKERAM')
    ap.add_argument('--clk-transition-ps', type=float, default=20)
    ap.add_argument('--uncertainty-ps', type=float, default=100)
    ap.add_argument('--hold-uncertainty-ps', type=float, default=50)
    ap.add_argument('--io-min-ps', type=float, default=100)
    ap.add_argument('--mode', choices=['reg2reg','io'], default='reg2reg')
    ap.add_argument('--io-fraction', type=float, default=.2)
    ap.add_argument('--input-transition-ps', type=float, default=50)
    ap.add_argument('--output-load-ff', type=float, default=4)
    ap.add_argument('--customization', type=Path)
    for stage in ('generic', 'map', 'opt'):
        ap.add_argument('--'+stage+'-effort', choices=['low','medium','high'])
    ap.add_argument('--save-checkpoint', action='append', default=[], choices=CHECKPOINTS)
    ap.add_argument('--resume-checkpoint', type=Path)
    args = ap.parse_args()
    custom = load_customization(args.customization)
    resume = validate_checkpoint(args.resume_checkpoint,args,custom) if args.resume_checkpoint else None
    out = args.out_dir.resolve()
    for name in ['logs','reports','netlist']:
        (out / name).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if args.probe:
        script = out / 'probe.tcl'
        script.write_text('puts "CORAL_GENUS_LICENSE_OK"\nexit\n')
        marker = 'CORAL_GENUS_LICENSE_OK'
    else:
        required = ['rtl','blackboxes','std_lib','sram_lib_dir','spec','netlist','script','top']
        for key in required:
            if getattr(args, key) is None:
                ap.error('--' + key.replace('_','-') + ' is required without --probe')
        script = args.script.resolve()
        mapping = dict(TOP=args.top, OUT=out, THREADS=args.threads, EFFORT=args.effort, HIERARCHY=args.hierarchy,
                       STD_LIB=args.std_lib.resolve(), SRAM_LIB_DIR=args.sram_lib_dir.resolve(),
                       BLACKBOXES=args.blackboxes.resolve(), RTL=args.rtl.resolve(),
                       DEFINES=args.defines, CLK_PORT=args.clk_port, PERIOD_PS=args.period_ps,
                       CLK_TRANSITION_PS=args.clk_transition_ps, UNCERTAINTY_PS=args.uncertainty_ps, HOLD_UNCERTAINTY_PS=args.hold_uncertainty_ps, IO_MIN_PS=args.io_min_ps,
                       STA_MODE=args.mode, IO_FRACTION=args.io_fraction,
                       INPUT_TRANSITION_PS=args.input_transition_ps, OUTPUT_LOAD_FF=args.output_load_ff)
        mapping.update(CHILD_NETLISTS='\n'.join(str(p.resolve()) for p in args.child_netlist),
                       PRESERVE_BLOCKS=' '.join(args.preserve_block))
        env.update({'FLOW_' + k: str(v) for k,v in mapping.items()})
        env.update(environment(custom))
        env.update(FLOW_SAVE_CHECKPOINTS=' '.join(args.save_checkpoint), FLOW_RESUME_STAGE='',
                   FLOW_GENERIC_EFFORT=args.generic_effort or args.effort,
                   FLOW_MAP_EFFORT=args.map_effort or args.effort,
                   FLOW_OPT_EFFORT=args.opt_effort or args.effort)
        if resume:
            origin = args.resume_checkpoint.resolve().parent
            env.update(FLOW_RESUME_STAGE=resume['stage'], FLOW_RESUME_DB=str(origin/'design.db'),
                       FLOW_RESUME_VERSION=str(origin/'tool-version.txt'))
        marker = 'CORAL_GENUS_SYNTHESIS_OK'
    identities = {stage:checkpoint_identity(args,stage,custom) for stage in args.save_checkpoint}
    log = out / 'logs/genus.log'
    with log.open('w') as fh:
        result = subprocess.run([args.genus, '-no_gui', '-batch', '-files', str(script)],
                                cwd=out, env=env, stdout=fh, stderr=subprocess.STDOUT)
    # Some startup/license/Tcl errors are reported despite exit code zero.
    text = log.read_text(errors='replace')
    if not args.probe:
        seal_checkpoints(out,args,custom,text,identities)
    if result.returncode or not any(line.strip() == marker for line in text.splitlines()):
        raise SystemExit(f'Genus failed (exit {result.returncode}, completion marker missing={marker not in text}); inspect {log}')
    if args.probe:
        print('PASS: licensed Genus Tcl execution; ' + str(log))
        return
    raw = out / 'netlist/genus-raw.v'
    if not raw.is_file() or not raw.stat().st_size:
        raise SystemExit('Genus produced no mapped netlist')
    libraries = [args.std_lib.resolve()] + sorted(args.sram_lib_dir.resolve().glob('*.lib'))
    reads = ''.join('read_liberty -lib %s; ' % yosys_quote(p) for p in libraries)
    netlist = args.netlist.resolve()
    child_reads = ''.join('read_verilog -sv %s%s; ' % ('-lib ' if args.unit_mode else '', yosys_quote(p.resolve())) for p in args.child_netlist)
    # Genus can leave unused bits of internal helper outputs undriven after
    # optimizing their callers. Flatten helper hierarchy and remove dead
    # wiring before checking drivers. Keep separately mapped child blocks.
    unit_cleanup = ''
    if args.unit_mode:
        unit_cleanup = ''.join('blackbox %s; ' % n for n in args.preserve_block)
        unit_cleanup += 'flatten; opt_clean -purge; '
    else:
        # Drop unused ports inside vendor-generated helper hierarchy before driver checks.
        unit_cleanup = 'flatten; opt_clean -purge; '
    normalized_raw = unit_source(raw, args.child_netlist, out / 'netlist/genus-unit.v') if args.unit_mode else raw
    script_text = reads + child_reads + ('read_verilog -sv -overwrite %s; hierarchy -check -top %s; '
                          '%s'
                          'check -mapped -assert; splitnets; '
                          'write_verilog -noattr -noexpr -nohex -nodec %s' %
                          (yosys_quote(normalized_raw), args.top, unit_cleanup, yosys_quote(netlist)))
    with (out / 'logs/normalize.log').open('w') as fh:
        subprocess.run(shlex.split(args.yosys) + ['-p', script_text],
                       stdout=fh, stderr=subprocess.STDOUT, check=True)
    counts = {} if args.unit_mode else validate_macros(netlist, args.top, args.spec.resolve(), libraries)
    (out / 'reports/genus-validation.json').write_text(json.dumps(
        dict(top=args.top, backend='genus', clock_period_ps=args.period_ps,
             threads=args.threads, effort=args.effort, hierarchy=args.hierarchy, cell_counts=counts,
             normalization='Yosys formatting only; no synthesis or technology remapping'), indent=2)+'\n')
    print('PASS: Genus mapping and netlist validation' + ('; SRAM counts deferred to assembly' if args.unit_mode else ' and SRAM instance counts'))

def unit_source(raw, children, output):
    """Keep owned modules only; Genus write_hdl also includes preserved children.

    Child module interfaces are loaded separately from their checked netlists.
    Whole-core assembly retains every complete child implementation.
    """
    import re
    pattern = re.compile(r'^\s*module\s+(\\\S+|[\w$]+)')
    excluded = set()
    for path in children:
        with Path(path).open() as stream:
            for line in stream:
                match = pattern.match(line)
                if match: excluded.add(match.group(1).lstrip('\\'))
    keep = True; seen = set()
    with Path(raw).open() as source, Path(output).open('w') as target:
        for line in source:
            match = pattern.match(line)
            if match:
                name = match.group(1).lstrip('\\')
                if name in seen: raise ValueError('Duplicate raw module: '+name)
                seen.add(name);keep = name not in excluded
            if keep: target.write(line)
            if re.match(r'^\s*endmodule\b', line): keep = True
    return output

if __name__ == '__main__':
    if not os.environ.get('CORAL_GENUS_ENTRY'):
        raise SystemExit('Use Synthesis-Flow-Kit/genus/run.py; this is an internal backend.')
    main()
