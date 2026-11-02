"""Complete combinational proofs by independent output cones, at most 8 CPUs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import genus_cec
from genus_lsu import run, sha


def aig_interface(path):
    with path.open('rb') as stream:
        header = stream.readline().decode().split()
        if len(header) != 6 or header[0] != 'aig' or int(header[3]) != 0:
            raise ValueError('Proof cones require basic combinational AIGER')
        inputs, outputs, ands = map(int, (header[2], header[4], header[5]))
        for _ in range(outputs):
            stream.readline()
        for _ in range(2 * ands):
            while True:
                byte = stream.read(1)
                if not byte:
                    raise ValueError('Truncated AIGER')
                if not byte[0] & 128:
                    break
        symbols = {'i': {}, 'o': {}}
        for row in stream.read().decode(errors='strict').splitlines():
            if row == 'c':
                break
            match = re.fullmatch(r'([io])(\d+) (.+)', row)
            if not match:
                raise ValueError('Missing or unsupported AIGER port symbol')
            direction, index, name = match.groups()
            index = int(index)
            if index in symbols[direction]:
                raise ValueError('Duplicate AIGER port symbol')
            symbols[direction][index] = name
        if set(symbols['i']) != set(range(inputs)) or set(symbols['o']) != set(range(outputs)):
            raise ValueError('Every AIGER input/output must have an explicit identity')
        return [symbols['i'][i] for i in range(inputs)], [symbols['o'][i] for i in range(outputs)]


def positive(log):
    return ('Networks are equivalent.' in log or
            'Networks are equivalent after structural hashing.' in log) and 'NOT EQUIVALENT' not in log


def prove_partitions(abc, out, workers=8):
    if not 1 <= workers <= 8:
        raise ValueError('CEC worker count must be 1..8')
    gold_inputs, gold_outputs = aig_interface(out / 'gold.aig')
    if aig_interface(out / 'gate.aig') != (gold_inputs, gold_outputs):
        raise ValueError('Full proof interfaces differ')
    key = hashlib.sha256((sha(out / 'gold.aig') + sha(out / 'gate.aig') + sha(Path(__file__))).encode()).hexdigest()
    parts = out / 'parts'; parts.mkdir(exist_ok=True)

    def check(indices):
        folder = parts / (str(indices[0]) + '-' + str(indices[-1])); folder.mkdir(exist_ok=True)
        proof = folder / 'proof.json'; log = folder / 'cec.log'
        cached = json.loads(proof.read_text()) if proof.exists() else {}
        if cached.get('key') == key and cached.get('indices') == indices and cached.get('status') == 'passed' and log.exists() and cached.get('log_sha256') == sha(log) and positive(log.read_text()):
            return [cached]
        for label in ('gold', 'gate'):
            command = f'read_aiger {out/(label+".aig")}; cone -a -N ' + ','.join(map(str, indices)) + f'; write_aiger -s {folder/(label+".aig")}'
            with (folder / (label + '-extract.log')).open('w') as stream:
                subprocess.run(abc + ['-c', command], stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=60)
            if aig_interface(folder / (label + '.aig')) != (gold_inputs, [gold_outputs[i] for i in indices]):
                raise ValueError('Cone extraction changed a primary input or selected output identity')
        timeout = False
        with log.open('w') as stream:
            try:
                subprocess.run(abc + ['-c', f'cec -T 120 {folder/"gold.aig"} {folder/"gate.aig"}'], stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=150)
            except subprocess.TimeoutExpired:
                timeout = True
        result = log.read_text()
        if positive(result) and not timeout:
            record = dict(status='passed', key=key, indices=indices, log=str(log), log_sha256=sha(log))
            proof.write_text(json.dumps(record, indent=2) + '\n')
            print(f'PASS: CEC outputs {indices[0]}..{indices[-1]}', flush=True)
            return [record]
        if 'NOT EQUIVALENT' in result:
            raise RuntimeError('Mapped gate mismatch: ' + str(log))
        if not timeout and not any(word in result.lower() for word in ('timed out', 'undecided', 'timeout')):
            raise RuntimeError('CEC command did not prove or time out: ' + str(log))
        if len(indices) == 1:
            raise RuntimeError('CEC single-output proof timed out: ' + str(log))
        midpoint = len(indices) // 2
        return check(indices[:midpoint]) + check(indices[midpoint:])

    groups = [list(range(i, min(i + 64, len(gold_outputs)))) for i in range(0, len(gold_outputs), 64)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = [record for group in pool.map(check, groups) for record in group]
    covered = [i for record in records for i in record['indices']]
    if sorted(covered) != list(range(len(gold_outputs))) or len(set(covered)) != len(covered):
        raise ValueError('Proof must cover every output exactly once')
    return records


def prove_partitioned_helper(name, rtl, mapped, std_lib, yosys, folder):
    executable = Path(shutil.which(yosys[0]) or yosys[0]).resolve().with_name('yosys-abc')
    if not executable.is_file():
        raise RuntimeError('yosys-abc required')
    abc = [str(executable)]
    versions = subprocess.check_output(yosys + ['-V']) + subprocess.check_output(abc + ['-c', 'version'])
    payload = rtl.read_bytes() + mapped.read_bytes() + std_lib.read_bytes()
    legacy_key = hashlib.sha256(payload + Path(genus_cec.__file__).read_bytes() + versions).hexdigest()
    key = hashlib.sha256(payload + Path(__file__).read_bytes() + versions).hexdigest()
    out = folder / 'mapped-cec'; out.mkdir(exist_ok=True)
    proof = out / 'proof.json'; log = out / 'cec.log'
    cached = json.loads(proof.read_text()) if proof.exists() else {}
    if cached.get('key') in (key, legacy_key) and cached.get('status') == 'passed' and log.exists() and cached.get('log_sha256') == sha(log) and positive(log.read_text()):
        if cached.get('key') == legacy_key:
            genus_cec.require_equivalent(log.read_text())
        else:
            for part in cached['partitions']:
                if sha(Path(part['log'])) != part['log_sha256'] or not positive(Path(part['log']).read_text()):
                    raise ValueError('Cached proof partition evidence changed')
            covered = [i for part in cached['partitions'] for i in part['indices']]
            if sorted(covered) != list(range(cached['output_bits'])):
                raise ValueError('Cached proof output coverage differs')
        return
    quote = lambda p: json.dumps(str(p))
    lines = []
    for label, path in [('gold', rtl), ('gate', mapped)]:
        if label == 'gate':
            lines.append(f'read_liberty -ignore_miss_func {quote(std_lib)}')
        lines += [f'read_verilog -sv {quote(path)}', f'hierarchy -check -top {name}', 'proc', 'flatten', 'opt_expr', 'opt_clean', 'techmap', 'opt_expr', 'opt_clean', 'aigmap', 'opt_clean', f'write_json {quote(out/(label+".json"))}', f'write_aiger -symbols -map {shlex.quote(str(out/(label+".map")))} {quote(out/(label+".aig"))}', 'design -reset']
    if any(c.isspace() for c in str(out)):
        raise ValueError('CEC output path cannot contain whitespace')
    script = out / 'export.ys'; script.write_text('\n'.join(lines) + '\n')
    run(yosys, script, out / 'export.log')
    interfaces = []
    for label in ('gold', 'gate'):
        module = json.loads((out / (label + '.json')).read_text())['modules'][name]
        if any(c['type'] not in ('$_AND_', '$_NOT_') and not (c['type'] == '$scopeinfo' and not c.get('connections')) for c in module['cells'].values()):
            raise ValueError('CEC contains state or unsupported cells')
        interfaces.append({n: (p['direction'], len(p['bits'])) for n, p in module['ports'].items()})
    if interfaces[0] != interfaces[1]:
        raise ValueError('CEC original external interfaces differ')
    counts = {d: sum(w for direction, w in interfaces[0].values() if direction == d) for d in ('input', 'output')}
    genus_cec.validate_ports(out / 'gold.map', out / 'gate.map', counts['input'], counts['output'])
    original_inputs, original_outputs = aig_interface(out / 'gold.aig')
    if len(original_inputs) != counts['input'] or len(original_outputs) != counts['output']:
        raise ValueError('AIGER adds free inputs or omits outputs')
    records = prove_partitions(abc, out)
    log.write_text('Networks are equivalent. Every output is covered exactly once by proved full-input cones.\n')
    proof.write_text(json.dumps(dict(status='passed', key=key, log_sha256=sha(log), input_bits=counts['input'], output_bits=counts['output'], port_map_sha256=sha(out/'gold.map'), gold_aig_sha256=sha(out/'gold.aig'), gate_aig_sha256=sha(out/'gate.aig'), engine='ABC output-cone equivalence; full original inputs; at most eight workers', partitions=records), indent=2) + '\n')
    print('PASS: complete partitioned native helper CEC ' + name, flush=True)
