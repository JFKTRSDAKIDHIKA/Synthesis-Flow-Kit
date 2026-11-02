"""Check native helper refinement of defined RTL outputs using explicit x masks.

Yosys xprop encodes four-state RTL as Boolean data and undefined masks. All
external inputs remain arbitrary defined bits. A mapped output must equal the
RTL wherever the RTL defines it, and must itself be defined there.
"""
import json
from pathlib import Path
from genus_lsu import run, sha
from genus_cec_parts import prove_partitioned_helper


def prove_defined_helper(name, rtl, mapped, std_lib, yosys, folder):
    out = folder / 'defined-cec'
    out.mkdir(exist_ok=True)
    quote = lambda p: json.dumps(str(p))
    modules = []
    interfaces = []
    for label, path in [('gold', rtl), ('gate', mapped)]:
        script = out / (label + '.ys')
        lines = [f'read_liberty -ignore_miss_func {quote(std_lib)}'] if label == 'gate' else []
        lines += [f'read_verilog -sv {quote(path)}', f'hierarchy -check -top {name}',
                  'proc', 'flatten', 'opt_expr -keepdc', 'opt_clean',
                  f'write_json {quote(out / (label + "-original.json"))}',
                  'xprop -split-ports -required -formal',
                  'opt', f'write_json {quote(out / (label + "-encoded.json"))}']
        script.write_text('\n'.join(lines) + '\n')
        run(yosys, script, out / (label + '-encode.log'))
        original = json.loads((out / (label + '-original.json')).read_text())['modules'][name]
        interfaces.append({n: (p['direction'], len(p['bits'])) for n, p in original['ports'].items()})
        data = json.loads((out / (label + '-encoded.json')).read_text())
        module = data['modules'][name]
        # No formal assumption may restrict the external input space.
        for cell_name, cell in list(module['cells'].items()):
            if cell['type'] == '$assume':
                connections = cell['connections']
                if connections['A'] != ['1'] and connections['EN'] != ['0']:
                    raise ValueError('CEC cannot discard a nontrivial xprop assumption')
                del module['cells'][cell_name]
        encoded_name = '__cec_' + label
        data['modules'] = {encoded_name: module}
        encoded = out / (label + '-encoded.json')
        encoded.write_text(json.dumps(data))
        script.write_text(f'read_json {quote(encoded)}\nwrite_verilog -noattr {quote(out / (label + "-encoded.v"))}\n')
        run(yosys, script, out / (label + '-verilog.log'))
        modules.append(module)
    if interfaces[0] != interfaces[1]:
        raise ValueError('Defined-output CEC external interfaces differ')
    ports = interfaces[0]
    if any(direction not in ('input', 'output') for direction, _ in ports.values()):
        raise ValueError('Defined-output CEC requires unidirectional ports')
    inputs = [(n, w) for n, (d, w) in ports.items() if d == 'input']
    outputs = [(n, w) for n, (d, w) in ports.items() if d == 'output']
    expected = {n + suffix: ('input', w) for n, w in inputs for suffix in ('_d', '_x')}
    expected.update({n + suffix: ('output', w) for n, w in outputs for suffix in ('_d', '_x')})
    for module in modules:
        if {n: (p['direction'], len(p['bits'])) for n, p in module['ports'].items()} != expected:
            raise ValueError('xprop must preserve every input and encode every output')
    violation_names = ['__cec_undefined_' + str(i) for i in range(len(outputs))]
    anchor = '__cec_anchor'
    if any(n in ports for n in violation_names + [anchor]):
        raise ValueError('CEC wrapper port collision')
    ident = lambda n: '\\' + n + ' '
    for label in ('gold', 'gate'):
        lines = [(out / 'gold-encoded.v').read_text()]
        if label == 'gate':
            lines.append((out / 'gate-encoded.v').read_text())
        all_names = list(ports) + violation_names + [anchor]
        lines.append('module ' + ident(name) + '(' + ','.join(ident(n) for n in all_names) + ');')
        # AIGER's map omits constant outputs. XOR with a fresh, unrestricted
        # common input keeps every checked bit in the map without changing
        # equality or constraining any original input.
        lines.append(f'input {anchor};')
        for n, (d, w) in ports.items():
            lines.append(f'{d} [{w-1}:0] {ident(n)};')
        for i, (n, w) in enumerate(outputs):
            lines.append(f'output [{w-1}:0] {violation_names[i]};')
            for prefix in ('g', 't'):
                lines.append(f'wire [{w-1}:0] {prefix}{i}d, {prefix}{i}x;')
            data = f'g{i}d' if label == 'gold' else f't{i}d'
            lines.append(f'assign {ident(n)} = ({data} & ~g{i}x) ^ {{{w}{{{anchor}}}}};')
            value = f'{w}\'b0' if label == 'gold' else f't{i}x & ~g{i}x'
            lines.append(f'assign {violation_names[i]} = ({value}) ^ {{{w}{{{anchor}}}}};')
        for source, prefix in [('gold', 'g')] + ([('gate', 't')] if label == 'gate' else []):
            connections = [f'.{ident(n+"_d")}({ident(n)})' for n, _ in inputs]
            connections += [f'.{ident(n+"_x")}({w}\'b0)' for n, w in inputs]
            connections += [f'.{ident(n+suffix)}({prefix}{i}{suffix[1:]})'
                            for i, (n, _) in enumerate(outputs) for suffix in ('_d', '_x')]
            lines.append(f'__cec_{source} {source}_instance (' + ','.join(connections) + ');')
        lines.append('endmodule')
        (out / (label + '.v')).write_text('\n'.join(lines) + '\n')
    prove_partitioned_helper(name, out / 'gold.v', out / 'gate.v', std_lib, yosys, folder)
    proof_path = folder / 'mapped-cec/proof.json'
    proof = json.loads(proof_path.read_text())
    proof.update(relation='mapped outputs equal every defined RTL output; mapped undefinedness forbidden wherever RTL is defined',
                 original_interface=ports, original_rtl_sha256=sha(rtl), original_gate_sha256=sha(mapped),
                 xprop_driver_sha256=sha(Path(__file__)))
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
