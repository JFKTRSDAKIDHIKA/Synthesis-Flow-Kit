#!/usr/bin/env python3
"""The supported entry point for new Genus synthesis and frozen Genus replay."""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
from resource_runtime import execute, available, ResourceRetry

PATH_KEYS = ('rtl', 'blackboxes', 'std_lib', 'sram_lib_dir', 'spec')
OPTIONS = {'threads', 'period_ps', 'effort', 'defines', 'blocks', 'yosys_boundaries',
           'repair_rvv_genvars', 'genvar_repair_proof', 'flatten_blocks', 'clean_blocks',
           'factor_mt', 'factor_lsu', 'factor_lsu_state', 'extract_element_cones',
           'reuse_from', 'clk_port', 'hierarchy', 'mode',
           'clk_transition_ps', 'uncertainty_ps', 'hold_uncertainty_ps', 'io_min_ps',
           'io_fraction', 'input_transition_ps', 'output_load_ff'}
BLOCK_ONLY = {'blocks', 'yosys_boundaries', 'repair_rvv_genvars', 'genvar_repair_proof',
              'flatten_blocks', 'clean_blocks', 'factor_mt', 'factor_lsu', 'factor_lsu_state',
              'extract_element_cones', 'reuse_from'}
FLAT_ONLY = {'clk_port', 'hierarchy', 'mode', 'clk_transition_ps', 'uncertainty_ps',
             'hold_uncertainty_ps', 'io_min_ps', 'io_fraction', 'input_transition_ps', 'output_load_ff'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def tool(value, base, command=False):
    local = (base / value).resolve()
    parts = [str(local)] if local.is_file() else shlex.split(value)
    if not parts or (not command and len(parts) != 1):
        raise ValueError('Tool must be an executable path: ' + value)
    resolved = shutil.which(parts[0])
    if not resolved:
        raise ValueError('Tool not found; source the workspace environment: ' + parts[0])
    parts[0] = str(Path(resolved).resolve())
    return shlex.join(parts) if command else parts[0]


def path_value(value, base):
    return (base / value).resolve()


def source_hashes():
    paths = [ROOT / 'run.py']
    for folder in ('scripts', 'tcl', 'rtl', 'tests'):
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}


def configuration(path, stage):
    path = path.resolve()
    config_bytes = path.read_bytes()
    c = json.loads(config_bytes)
    if not isinstance(c,dict): raise ValueError('Configuration must be a JSON object')
    allowed = {'schema_version', 'platform_id', 'flow', 'top', *PATH_KEYS, 'out_dir',
               'tools', 'options', 'lock_file', 'admission_gib', 'input_sha256'}
    unknown = set(c) - allowed
    if unknown:
        raise ValueError('Unknown configuration keys: ' + ', '.join(sorted(unknown)))
    if c.get('schema_version') != 1 or not c.get('platform_id') or not c.get('top'):
        raise ValueError('schema_version=1, explicit platform_id and top are required')
    if c.get('flow') not in ('blocks', 'flat'):
        raise ValueError('flow must be blocks or flat')
    base = path.parent
    inputs = {}
    for key in PATH_KEYS:
        p = path_value(c[key], base)
        if not (p.is_dir() if key == 'sram_lib_dir' else p.is_file()):
            raise ValueError('Missing input ' + key + ': ' + str(p))
        inputs[key] = p
    out = path_value(c['out_dir'], base)
    if out == ROOT or ROOT in out.parents:
        raise ValueError('Outputs must be outside genus/; scripts and results must not mix')
    for key,p in inputs.items():
        if p == out or out in p.parents:
            raise ValueError('Inputs must not live inside the output directory: ' + key)
    options = dict(c.get('options', {}))
    unknown = set(options) - OPTIONS
    if unknown:
        raise ValueError('Unknown options: ' + ', '.join(sorted(unknown)))
    incompatible = set(options) & (FLAT_ONLY if c['flow'] == 'blocks' else BLOCK_ONLY)
    if incompatible:
        raise ValueError('Options incompatible with flow: ' + ', '.join(sorted(incompatible)))
    options.setdefault('threads', 2)
    options.setdefault('effort', 'medium')
    if type(options['threads']) is not int or options['threads'] < 1:
        raise ValueError('threads must be a positive integer')
    if 'period_ps' not in options or type(options['period_ps']) is not int or options['period_ps'] <= 0:
        raise ValueError('An explicit positive integer period_ps is required')
    if c['flow'] == 'blocks' and not options.get('blocks'):
        raise ValueError('Explicit block boundaries are required for bottom-up synthesis')
    if c['flow'] == 'flat' and not options.get('clk_port'):
        raise ValueError('An explicit clock port is required for flat synthesis')
    tools = c.get('tools', {})
    if set(tools) - {'genus', 'yosys'}:
        raise ValueError('Only genus and yosys tool keys are supported')
    genus = tool(tools.get('genus', 'genus'), base)
    yosys = tool(tools.get('yosys', 'yosys'), base, command=True)
    script = ROOT / 'scripts' / ('genus_blocks.py' if c['flow'] == 'blocks' else 'run_genus.py')
    cmd = [sys.executable, str(script), '--genus', genus, '--yosys', yosys, '--top', c['top']]
    for key,p in inputs.items():
        cmd += ['--' + key.replace('_','-'), str(p)]
    cmd += ['--out-dir', str(out), '--netlist', str(out / 'mapped.v')]
    if c['flow'] == 'blocks':
        cmd += ['--jobs', '1']
        if stage == 'plan': cmd += ['--plan-only']
    elif stage == 'plan':
        raise ValueError('plan is a Yosys-only bottom-up stage; flat flow supports check and synth')
    else:
        cmd += ['--script', str(ROOT / 'tcl/genus.tcl')]
        options.setdefault('hierarchy', 'flat')
        options.setdefault('mode', 'io')
    for key,value in options.items():
        if key in ('reuse_from','genvar_repair_proof'):
            value = str(path_value(value, base))
            if not Path(value).exists(): raise ValueError('Missing ' + key + ': ' + value)
        if isinstance(value, bool):
            if value: cmd += ['--' + key.replace('_','-')]
        else:
            if isinstance(value, list): value = ' '.join(map(str,value))
            cmd += ['--' + key.replace('_','-'), str(value)]
    pins = {str(p):sha(p) for key,p in inputs.items() if key != 'sram_lib_dir'}
    pins.update({str(p):sha(p) for p in sorted(inputs['sram_lib_dir'].glob('*.lib'))})
    if not any(inputs['sram_lib_dir'].glob('*.lib')):
        # Empty macro specs are useful for logic-only designs; otherwise fail closed.
        if json.loads(inputs['spec'].read_text()).get('macros'):
            raise ValueError('Macro spec is nonempty but SRAM library directory contains no Liberty files')
    for name,expected in c.get('input_sha256', {}).items():
        p=path_value(name,base)
        if sha(p) != expected: raise ValueError('Pinned input changed: ' + str(p))
        pins[str(p)]=expected
    # Keep optional cache/proof inputs in the launch record as identities too.
    if options.get('genvar_repair_proof'):
        p=path_value(options['genvar_repair_proof'],base);pins[str(p)]=sha(p)
    lock = path_value(c['lock_file'], base)
    if lock == out or out in lock.parents: raise ValueError('The shared lock must be outside the output directory')
    if lock in inputs.values() or str(lock) in pins: raise ValueError('The shared lock cannot be an input file')
    if lock == ROOT or ROOT in lock.parents: raise ValueError('The shared lock must be outside genus/')
    admission = c.get('admission_gib', 36)
    if not isinstance(admission,(int,float)) or admission < 0: raise ValueError('admission_gib must be nonnegative')
    return c, out, lock, dict(schema_version=1, platform_id=c['platform_id'], stage=stage,
        configuration=str(path), configuration_sha256=hashlib.sha256(config_bytes).hexdigest(), command=cmd,
        inputs_sha256=pins, sources_sha256=source_hashes(),
        tools_sha256={genus:sha(genus), shlex.split(yosys)[0]:sha(shlex.split(yosys)[0])},
        resource_policy=dict(threads=options['threads'], native_jobs=1, admission_gib=admission,
            pause_below_gib=8,resume_above_gib=12,own_pss_limit_gib=32,sampled_only=True),
        scope=('Yosys-only elaboration and structural plan; no native mapping or timing qualification' if stage=='plan' else
               'Native synthesis and structural checks only; timing closure, equivalence and physical closure require separate evidence'))


def run_config(stage, config):
    c,out,lock,manifest = configuration(config,stage)
    if stage == 'check':
        print(json.dumps(manifest, indent=2))
        return
    if out.exists() and any(out.iterdir()):
        raise ValueError('Preserve existing results; choose a fresh out_dir: ' + str(out))
    lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+') as slot:
        try: fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ResourceRetry('Shared Genus slot is occupied')
        if stage == 'synth' and available() < manifest['resource_policy']['admission_gib'] * (1<<30):
            raise ResourceRetry('Insufficient host RAM headroom; retry later with the same fresh configuration')
        out.mkdir(parents=True,exist_ok=True)
        snapshot=out/'flow-source'
        for relative,digest in manifest['sources_sha256'].items():
            original=ROOT/relative
            if sha(original)!=digest:raise RuntimeError('Source changed before snapshot: '+relative)
            target=snapshot/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(original,target)
        manifest['command'][1]=str(snapshot/'scripts'/Path(manifest['command'][1]).name)
        if '--script' in manifest['command']:
            index=manifest['command'].index('--script')+1
            manifest['command'][index]=str(snapshot/'tcl'/Path(manifest['command'][index]).name)
        write_json(out/'launch.json',manifest)
        if sha(config)!=manifest['configuration_sha256']:raise RuntimeError('Configuration changed before launch')
        shutil.copy2(config, out/'configuration.json')
        if sha(out/'configuration.json')!=manifest['configuration_sha256']:raise RuntimeError('Configuration changed while snapshotting')
        env={'CORAL_GENUS_ENTRY':str(ROOT/'run.py'), 'OMP_NUM_THREADS':'1',
             'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'}
        if os.environ.get('CDS_LIC_FILE'):
            env['LM_LICENSE_FILE']=''  # Cadence uses its selected CDS_LIC_FILE.
        status=out/'entry-status.json'
        write_json(status,dict(state='running',stage=stage,started_unix=time.time()))
        def update(metrics): write_json(out/'resources.json',metrics)
        try:
            execute(manifest['command'],out,out/'entry-console.log',update,env)
            if stage=='synth':
                native=out/'native-final';native.mkdir()
                opts=c.get('options',{})
                sdc=(out/'units'/c['top']/'reports/constraints.sdc' if c['flow']=='blocks'
                     else out/'reports/constraints.sdc')
                if not sdc.is_file():raise RuntimeError('Backend produced no top-level SDC')
                spec=json.loads(Path(manifest['command'][manifest['command'].index('--spec')+1]).read_text())
                expected=[]
                for macro in spec['macros']:
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',macro['name']):
                        raise ValueError('Unsafe macro name')
                    expected.append(macro['name']+' '+str(int(macro['instances'])))
                native_env=dict(env,FLOW_TOP=c['top'],FLOW_OUT=str(native),FLOW_RTL=str(out/'mapped.v'),
                    FLOW_SDC=str(sdc),FLOW_STD_LIB=str(path_value(c['std_lib'],config.resolve().parent)),
                    FLOW_SRAM_LIB_DIR=str(path_value(c['sram_lib_dir'],config.resolve().parent)),
                    FLOW_THREADS=str(manifest['resource_policy']['threads']),FLOW_PERIOD_PS=str(opts['period_ps']),
                    FLOW_EXPECTED_MACROS='\n'.join(expected))
                command=[manifest['command'][manifest['command'].index('--genus')+1],'-no_gui','-batch',
                         '-files',str(snapshot/'tcl/final-check.tcl')]
                write_json(native/'command.json',dict(command=command,environment=native_env))
                execute(command,native,native/'console.log',update,native_env)
                text=(native/'console.log').read_text(errors='replace')
                if not any(line.strip()=='CORAL_GENUS_FINAL_CHECK_OK' for line in text.splitlines()):
                    raise RuntimeError('Final native restore did not reach its completion marker')
                if re.search(r'\*\*ERROR:|^Error\s*:',text,re.M):
                    raise RuntimeError('Final native restore reported an error')
            for name,digest in manifest['inputs_sha256'].items():
                if sha(name)!=digest: raise RuntimeError('Input changed during the run: '+name)
            if sha(config)!=manifest['configuration_sha256']:raise RuntimeError('Configuration changed during the run')
            if source_hashes()!=manifest['sources_sha256']: raise RuntimeError('Flow source changed during the run')
            if stage=='synth' and (not (out/'mapped.v').exists() or not (out/'mapped.v').stat().st_size):
                raise RuntimeError('Backend returned without a mapped netlist')
            write_json(status,dict(state='complete',stage=stage,finished_unix=time.time(),
                scope=manifest['scope'], mapped_sha256=sha(out/'mapped.v') if stage=='synth' else None))
        except BaseException as exc:
            write_json(status,dict(state='resource-retry' if isinstance(exc,ResourceRetry) else 'failed',
                stage=stage,error=str(exc),finished_unix=time.time()))
            raise
    print(str(status))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('check','plan','synth'):
        q=sub.add_parser(name,help={'check':'Validate and print frozen launch intent; launch no tools',
            'plan':'Elaborate and qualify block boundaries with Yosys only',
            'synth':'Run Genus through the shared slot and sampled memory guard'}[name])
        q.add_argument('configuration',type=Path)
    q=sub.add_parser('prepare',help='Self-contained SRAM preparation helpers')
    q.add_argument('helper',choices=['models','bind','bind-wide','verify-wide'])
    q.add_argument('arguments',nargs=argparse.REMAINDER)
    q=sub.add_parser('legacy-mlem',help='Replay an explicitly frozen historical MLEM synthesis manifest')
    q.add_argument('manifest',type=Path)
    q.add_argument('--genus',default='genus')
    args=p.parse_args()
    if args.command=='legacy-mlem':
        from legacy_mlem import replay
        replay(args.manifest,args.genus,sha,write_json,ROOT/'run.py')
        return 0
    if args.command=='prepare':
        helper={'models':'prepare_asap7_sram_models.py','bind':'prepare_ameooo_asap7.py',
                'bind-wide':'prepare_ameooo_wide_sram.py','verify-wide':'verify_ameooo_wide_sram.py'}[args.helper]
        return subprocess.call([sys.executable,str(ROOT/'scripts'/helper),*args.arguments])
    run_config(args.command,args.configuration)
    return 0

if __name__=='__main__':
    try: sys.exit(main())
    except ResourceRetry as exc:
        print(str(exc),file=sys.stderr);sys.exit(75)
    except (ValueError,KeyError,RuntimeError,OSError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(2)
