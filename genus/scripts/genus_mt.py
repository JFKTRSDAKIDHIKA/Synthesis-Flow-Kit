"""Proof-gated factoring of the VME matrix read/write combinational logic.

The original storage module is retained. A read-port function and a write-bank
function are synthesized once and reused, avoiding a multi-million-instance
generic partition. All interface behavior is proved before replacing the RTL.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
RTL = HERE.parent / 'rtl/GenusMtFactor.sv'
HELPERS = ('GenusMtReadPort', 'GenusMtWriteBank')
PORTS = {'clk': ('input', 1), 'rst_n': ('input', 1),
         'readMtIdx': ('input', 64), 'readSubIdx': ('input', 64),
         'readData': ('output', 2048), 'writeEn': ('input', 256),
         'writeMtIdx': ('input', 64), 'writeSubIdx': ('input', 64),
         'writeData': ('input', 2048)}

def dump(path, data):
    path.write_text(json.dumps(data))

def run(yosys, script, log):
    with log.open('w') as stream:
        subprocess.run(yosys + ['-s', str(script)], stdout=stream,
                       stderr=subprocess.STDOUT, check=True)

def interface(module, name):
    """Cut unchanged storage, exposing arbitrary contents and active writes."""
    module = copy.deepcopy(module)
    registers = [n for n, c in module['cells'].items() if c['type'] == 'MTStorage']
    if len(registers) != 1:
        raise ValueError('matrix factoring requires one unchanged storage child')
    register = module['cells'].pop(registers[0])['connections']
    if any(c['type'].startswith('$') and any(k in c['type'].lower() for k in ('ff', 'latch', 'mem'))
           for c in module['cells'].values()):
        raise ValueError('matrix wrapper is not combinational')
    if any(len(register[p]) != n for p, n in
           [('mt', 32768), ('wdata', 32768), ('wen', 4096), ('clk', 1), ('rst_n', 1)]):
        raise ValueError('matrix storage geometry changed')
    module['ports']['memory_contents'] = dict(direction='input', bits=register['mt'])
    for port, child in [('memory_write_enable', 'wen'), ('memory_clock', 'clk'),
                        ('memory_reset_n', 'rst_n')]:
        module['ports'][port] = dict(direction='output', bits=register[child])
    bits = [b for c in module['cells'].values() for v in c['connections'].values()
            for b in v if isinstance(b, int)]
    bits += [b for p in module['ports'].values() for b in p['bits'] if isinstance(b, int)]
    bits += [b for connection in register.values() for b in connection if isinstance(b, int)]
    active = list(range(max(bits) + 1, max(bits) + 32769))
    module['ports']['memory_active_write_data'] = dict(direction='output', bits=active)
    module['cells']['observe_active_write_data'] = dict(
        hide_name=0, type='$and', attributes={},
        parameters={'A_SIGNED': '0', 'B_SIGNED': '0', **{
            p: format(32768, '032b') for p in ('A_WIDTH', 'B_WIDTH', 'Y_WIDTH')}},
        port_directions={'A': 'input', 'B': 'input', 'Y': 'output'},
        connections={'A': register['wdata'],
                     'B': [b for b in register['wen'] for _ in range(8)], 'Y': active})
    module['netnames'] = {p: dict(hide_name=0, bits=v['bits'], attributes={})
                          for p, v in module['ports'].items()}
    return dict(modules={name: module})

def factor_matrix(data, yosys, out):
    modules = data['modules']
    matrices = [n for n in modules if n == 'zvt_mt' or n.startswith('zvt_mt$')]
    if len(matrices) != 1 or any(n in modules for n in HELPERS):
        raise ValueError('matrix factoring requires one VME wrapper and fresh helper names')
    name = matrices[0]
    original = modules[name]
    shape = {p: (v['direction'], len(v['bits'])) for p, v in original['ports'].items()}
    if shape != PORTS:
        raise ValueError('unsupported matrix geometry; do not silently change storage or ports')
    stores = [c for c in original['cells'].values()
              if c['type'] == 'zvt_mt_reg' or c['type'].startswith('zvt_mt_reg$')]
    if len(stores) != 1:
        raise ValueError('matrix storage boundary missing')
    storage_type = stores[0]['type']
    out = out / 'matrix-factor'
    out.mkdir(parents=True, exist_ok=True)
    source = json.dumps(original, sort_keys=True).encode()
    key = hashlib.sha256(source + RTL.read_bytes() + Path(__file__).read_bytes()
                         + subprocess.check_output(yosys + ['-V'])).hexdigest()
    script = out / 'elaborate.ys'
    script.write_text(f'read_verilog -sv {json.dumps(str(RTL))}\nproc\n'
                      f'write_json {json.dumps(str(out / "candidate.json"))}\n')
    run(yosys, script, out / 'elaborate.log')
    candidate = json.loads((out / 'candidate.json').read_text())
    proof = out / 'proof.json'
    cached = json.loads(proof.read_text()) if proof.is_file() else {}
    log = out / 'equivalence.log'
    if (cached.get('key') != key or cached.get('status') != 'passed'
            or not log.is_file() or cached.get('log_sha256') != hashlib.sha256(log.read_bytes()).hexdigest()):
        gold = copy.deepcopy(original)
        for c in gold['cells'].values():
            if c['type'] == storage_type:
                c['type'] = 'MTStorage'
        dump(out / 'original.json', dict(modules={'gold_source': gold,
             'MTStorage': candidate['modules']['MTStorage']}))
        prepare = out / 'prepare-proof.ys'
        prepare.write_text(f'read_json {json.dumps(str(out / "original.json"))}\n'
            'hierarchy -check -top gold_source\nbwmuxmap\nbmuxmap\ndemuxmap\n'
            f'write_json {json.dumps(str(out / "original-lowered.json"))}\n'
            'design -reset\n'
            f'read_json {json.dumps(str(out / "candidate.json"))}\n'
            'hierarchy -check -top GenusMtWrapper\nflatten\nopt_clean -purge\n'
            f'write_json {json.dumps(str(out / "candidate-flat.json"))}\n')
        print('Proving matrix factoring: arbitrary contents, read outputs, byte writes and priority', flush=True)
        run(yosys, prepare, out / 'prepare-proof.log')
        for source_name, top, dest in [('original-lowered', 'gold_source', 'gold'),
                                       ('candidate-flat', 'GenusMtWrapper', 'clean')]:
            module = json.loads((out / (source_name + '.json')).read_text())['modules'][top]
            dump(out / (dest + '.json'), interface(module, dest))
        check = out / 'equivalence.ys'
        check.write_text(''.join(f'read_json {json.dumps(str(out / (n + ".json")))}\n'
                                for n in ('gold', 'clean')) +
            'equiv_make gold clean equiv\nhierarchy -top equiv\n'
            'opt_expr\nwreduce\nopt_clean -purge\nopt_merge\n'
            'techmap\nopt_expr\nopt_merge\nopt_clean -purge\n'
            'equiv_simple -short -nogroup\nequiv_status -assert\n')
        run(yosys, check, out / 'equivalence.log')
        dump(proof, dict(key=key, status='passed', original_wrapper=name,
             unchanged_storage=storage_type, rtl_sha256=hashlib.sha256(RTL.read_bytes()).hexdigest(),
             log_sha256=hashlib.sha256((out / 'equivalence.log').read_bytes()).hexdigest(),
             scope='arbitrary 32768-bit contents; all reads, byte enables, active write data, clock/reset'))
    print('PASS: matrix factoring proved; original 32768-bit storage retained', flush=True)
    rewritten = candidate['modules']['GenusMtWrapper']
    for c in rewritten['cells'].values():
        if c['type'] == 'MTStorage':
            c['type'] = storage_type
    modules[name] = rewritten
    for helper in HELPERS:
        modules[helper] = candidate['modules'][helper]
    return HELPERS

def prove_mapped_helper(name, rtl, mapped, std_lib, yosys, folder):
    """Verify the native gate implementation of each new combinational helper."""
    key = hashlib.sha256(rtl.read_bytes() + mapped.read_bytes() + std_lib.read_bytes()
                         + Path(__file__).read_bytes()
                         + subprocess.check_output(yosys + ['-V'])).hexdigest()
    record = folder / 'mapped-equivalence.json'
    log = folder / 'mapped-equivalence.log'
    cached = json.loads(record.read_text()) if record.is_file() else {}
    if (cached.get('key') == key and cached.get('status') == 'passed'
            and log.is_file() and cached.get('log_sha256') == hashlib.sha256(log.read_bytes()).hexdigest()):
        return
    script = folder / 'mapped-equivalence.ys'
    script.write_text(f'read_verilog -sv {json.dumps(str(rtl))}\nproc\n'
        f'flatten {name}\nrename {name} gold\n'
        f'read_liberty -ignore_miss_func {json.dumps(str(std_lib))}\n'
        f'read_verilog -sv {json.dumps(str(mapped))}\n'
        f'flatten {name}\nrename {name} gate\n'
        'equiv_make gold gate equiv\nhierarchy -top equiv\n'
        'techmap\nopt_expr\nopt_merge\nopt_clean -purge\n'
        'equiv_simple -short -nogroup\nequiv_status -assert\n')
    run(yosys, script, log)
    dump(record, dict(key=key, status='passed',
                     log_sha256=hashlib.sha256(log.read_bytes()).hexdigest()))
    print('PASS: native Genus helper equivalence '+name, flush=True)
