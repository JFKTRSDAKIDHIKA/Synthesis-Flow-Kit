"""Use the existing Yosys boundary/tied-input planner, without ABC mapping."""
import hashlib
import json
from pathlib import Path
import subprocess
import shlex

HERE = Path(__file__).resolve().parent

def prepare(rtl, blackboxes, top, blocks, defines, yosys, out):
    out.mkdir(parents=True, exist_ok=True)
    planfile = out/'yosys-plan.json'
    subprocess.run(['python3', str(HERE/'block_plan.py'), '--rtl', str(rtl), '--top', top,
                    '--blocks', ' '.join(blocks), '--out', str(planfile)], check=True)
    plan = json.loads(planfile.read_text())
    units = {u['unit'] for u in plan['units']}
    merged = {}; stubs = {}
    for u in plan['units']:
        name = u['unit']; prefix = out/name; j = prefix.with_suffix('.json')
        options = ' '.join('--blackboxed-module '+b for b in u['blackboxes'])
        options += ' '+ ' '.join('-D'+d for d in defines.split())
        lines = [f'read_slang --best-effort-hierarchy --empty-blackboxes --top {u["rtl_top"]} {options} {shlex.quote(str(rtl))} {shlex.quote(str(blackboxes))}']
        if u['ties']:
            lines.append('cd '+u['rtl_top'])
            for port, value in sorted(u['ties'].items()):
                width = int(value.split("'", 1)[0])
                lines += [f'rename {port} {port}__tie', f'delete -port {port}__tie',
                          f'add -input {port} {width}', f'connect -set {port}__tie {value}']
            lines += ['cd ..', f'rename {u["rtl_top"]} {name}']
        intermediate = prefix.with_suffix('.il'); raw = prefix.with_suffix('.raw.json')
        readscript = prefix.with_suffix('.read.ys')
        readscript.write_text('\n'.join(lines+[f'write_rtlil {json.dumps(str(intermediate))}', f'write_json {json.dumps(str(raw))}'])+'\n')
        with prefix.with_suffix('.read.log').open('w') as log:
            subprocess.run(yosys+['-m','slang','-s',str(readscript)],stdout=log,stderr=subprocess.STDOUT,check=True)
        available = json.loads(raw.read_text())['modules']
        lines = [f'read_rtlil {json.dumps(str(intermediate))}']
        for block in plan['blocks']:
            if block['block'] not in u['blackboxes'] or block['block'] not in available: continue
            for sp in block['specializations']:
                if sp['ties']: lines.append('copy '+block['block']+' '+sp['unit'])
        lines += plan['retype']
        lines += ['hierarchy -check -top '+name, 'proc', 'memory_dff', 'memory_collect', 'bwmuxmap',
                  'write_json '+json.dumps(str(j))]
        script = prefix.with_suffix('.ys'); script.write_text('\n'.join(lines)+'\n')
        with prefix.with_suffix('.log').open('w') as log:
            subprocess.run(yosys+['-m', 'slang', '-s', str(script)], stdout=log, stderr=subprocess.STDOUT, check=True)
        data = json.loads(j.read_text())['modules']
        stubs.update({n:m for n,m in data.items() if m.get('attributes', {}).get('blackbox')})
        rename = {n:name+'__private_'+hashlib.sha256(n.encode()).hexdigest()[:10]
                  for n,m in data.items() if n not in units and not m.get('attributes', {}).get('blackbox')}
        for n,m in data.items():
            if m.get('attributes', {}).get('blackbox'): continue
            if n != name and n in units: raise ValueError('Unexpected other synthesis unit: '+n)
            m.setdefault('attributes', {}).pop('top', None)
            for cell in m.get('cells', {}).values(): cell['type'] = rename.get(cell['type'], cell['type'])
            merged[rename.get(n,n)] = m
    # Macro interfaces are needed by hierarchy checking; block stubs are replaced by full units.
    for n,m in stubs.items():
        if n not in merged and m.get('attributes', {}).get('blackbox'): merged[n] = m
    merged[top]['attributes']['top'] = '1'
    return {'modules':merged}, sorted(units-{top}), plan
