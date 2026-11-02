"""Correct only the certified RVV slang boundary metadata defect.

Real child bodies are already elaborated independently. Slang's empty
blackboxes retain final genvar values as overrides on three parent cells;
the actual nonparameterized child modules cannot accept those overrides.
"""
import hashlib
import json
from pathlib import Path
import re


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def repair(data, rtl, output, prior_proof=None):
    source = Path(rtl).read_text()
    source = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    expected = {
        ('u_falu', 'rvv_backend_falu'): {'i': format(1, '032b')},
        ('u_mulmac', 'rvv_backend_mulmac'): {'i': format(1, '032b')},
        ('u_vrf', 'rvv_backend_vrf'): {'j': format(5, '032b'), 'k': format(15, '032b')},
    }
    modules = data['modules']
    changes = []
    before = fingerprint(data)
    for parent, module in modules.items():
        for name, cell in module.get('cells', {}).items():
            target = modules.get(cell['type'])
            if target is None or cell['type'].startswith('$'):
                continue
            extras = set(cell.get('parameters', {})) - set(target.get('parameter_default_values', {}))
            if not extras:
                continue
            key = (name, cell['type'])
            assert parent.startswith('RvvCoreWrapper__'), (parent, name)
            assert key in expected and cell['parameters'] == expected[key], (parent, name, cell['parameters'])
            assert not target.get('parameter_default_values'), cell['type']
            body = re.search(r'\bmodule\s+' + re.escape(cell['type']) + r'\b\s*(.*?)\bendmodule\b', source, re.S)
            assert body and body[1].lstrip().startswith('('), 'Module must have no parameter header'
            for variable in extras:
                assert re.search(r'\bgenvar\s+[^;\n]*\b' + variable + r'\b', body[1]), variable
            changes.append(dict(parent=parent, cell=name, target=cell['type'], removed=dict(cell['parameters'])))
    if not changes:
        # Reusing a previously repaired snapshot: require its complete generic
        # identity and original source/proof, not merely absence of bad fields.
        if prior_proof is None:
            raise ValueError('An already-repaired snapshot requires an explicit --genvar-repair-proof')
        prior = Path(prior_proof).resolve()
        result = json.loads(prior.read_text())
        assert result['passed'] and result['exact_inverse_passed']
        assert before == result['after_sha256'], 'Repaired generic snapshot drift'
        assert hashlib.sha256(Path(rtl).read_bytes()).hexdigest() == result['source_sha256']
        result.update(reused_checked_proof=str(prior), already_repaired=True)
        Path(output).write_text(json.dumps(result, indent=2) + '\n')
        return result
    assert len(changes) == 3, changes
    for change in changes:
        modules[change['parent']]['cells'][change['cell']]['parameters'] = {}
    after = fingerprint(data)
    # Reconstruct the complete input to prove that no connectivity, port,
    # cell body, attribute or other parameter was modified.
    for change in changes:
        modules[change['parent']]['cells'][change['cell']]['parameters'] = change['removed']
    assert fingerprint(data) == before, 'Nonmetadata mutation'
    for change in changes:
        modules[change['parent']]['cells'][change['cell']]['parameters'] = {}
    assert fingerprint(data) == after
    result = dict(passed=True, before_sha256=before, after_sha256=after,
                  exact_inverse_passed=True, source_sha256=hashlib.sha256(Path(rtl).read_bytes()).hexdigest(),
                  changes=changes, scope='Only invalid genvar overrides removed; all hardware structures and connections preserved.')
    Path(output).write_text(json.dumps(result, indent=2) + '\n')
    return result
