"""Validate combinational Genus helpers with ABC CEC, never technology mapping."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from genus_lsu import run,sha


def require_equivalent(log):
    # ABC can return zero for a counterexample or command error.
    if 'Networks are equivalent.' not in log or 'NOT EQUIVALENT' in log:
        raise RuntimeError('CEC did not prove equivalence; inspect mapped-cec/cec.log')


def validate_ports(gold,gate,inputs,outputs):
    if gold.read_text()!=gate.read_text():
        raise ValueError('CEC primary input/output identities differ')
    rows=[line.split() for line in gold.read_text().splitlines()]
    if any(row[0] not in ('input','output') for row in rows):
        raise ValueError('CEC helpers must be purely combinational')
    if sum(row[0]=='input' for row in rows)!=inputs or sum(row[0]=='output' for row in rows)!=outputs:
        raise ValueError('CEC must cover every external input/output bit')


def prove_combinational_helper(name,rtl,mapped,std_lib,yosys,folder):
    executable=Path(shutil.which(yosys[0]) or yosys[0]).resolve().with_name('yosys-abc')
    if not executable.is_file():raise RuntimeError('yosys-abc is required alongside Yosys for helper CEC')
    abc=[str(executable)]
    version=subprocess.check_output(abc+['-c','version'])
    key=hashlib.sha256(rtl.read_bytes()+mapped.read_bytes()+std_lib.read_bytes()
        +Path(__file__).read_bytes()+subprocess.check_output(yosys+['-V'])+version).hexdigest()
    out=folder/'mapped-cec';out.mkdir(exist_ok=True)
    proof=out/'proof.json';log=out/'cec.log'
    cached=json.loads(proof.read_text()) if proof.exists() else {}
    if (cached.get('key')==key and cached.get('status')=='passed'
            and log.exists() and cached.get('log_sha256')==sha(log)):
        require_equivalent(log.read_text());return
    quote=lambda p:json.dumps(str(p))
    script=out/'export.ys';lines=[]
    for label,path in [('gold',rtl),('gate',mapped)]:
        if label=='gate':lines.append(f'read_liberty -ignore_miss_func {quote(std_lib)}')
        lines += [f'read_verilog -sv {quote(path)}',f'hierarchy -check -top {name}',
            'proc','flatten','opt_expr','opt_clean','techmap','opt_expr','opt_clean',
            'aigmap','opt_clean',f'write_json {quote(out/(label+".json"))}',
            f'write_aiger -symbols -map {shlex.quote(str(out/(label+".map")))} {quote(out/(label+".aig"))}',
            'design -reset']
    # write_aiger's -map argument does not process quoted strings. Require
    # a path without whitespace rather than risk silently writing elsewhere.
    if any(c.isspace() for c in str(out)):raise ValueError('CEC output path must not contain whitespace')
    script.write_text('\n'.join(lines)+'\n');run(yosys,script,out/'export.log')
    ports=[]
    for label in ('gold','gate'):
        module=json.loads((out/(label+'.json')).read_text())['modules'][name]
        if any(c['type'] not in ('$_AND_','$_NOT_')
                and not (c['type']=='$scopeinfo' and not c.get('connections'))
                for c in module['cells'].values()):
            raise ValueError('CEC helper contains state or unsupported cells')
        ports.append({n:(p['direction'],len(p['bits'])) for n,p in module['ports'].items()})
    if ports[0]!=ports[1]:raise ValueError('CEC external interfaces differ')
    counts={direction:sum(size for d,size in ports[0].values() if d==direction) for direction in ('input','output')}
    validate_ports(out/'gold.map',out/'gate.map',counts['input'],counts['output'])
    for label in ('gold','gate'):
        with (out/(label+'.aig')).open('rb') as stream:header=stream.readline().decode().split()
        if (header[0]!='aig' or int(header[2])!=counts['input'] or int(header[3])!=0
                or int(header[4])!=counts['output']):
            raise ValueError('CEC exported extra free inputs, state or missing outputs')
    with log.open('w') as stream:
        subprocess.run(abc+['-c',f'cec -T 600 {out/"gold.aig"} {out/"gate.aig"}'],
            stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=660)
    require_equivalent(log.read_text())
    proof.write_text(json.dumps(dict(status='passed',key=key,log_sha256=sha(log),
        input_bits=counts['input'],output_bits=counts['output'],
        port_map_sha256=sha(out/'gold.map'),gold_aig_sha256=sha(out/'gold.aig'),
        gate_aig_sha256=sha(out/'gate.aig'),engine='ABC combinational equivalence; name-matched full external interface'),indent=2)+'\n')
    print('PASS: native Genus helper CEC '+name,flush=True)
