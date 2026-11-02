"""Seal retained synthesis evidence and validate it before comparing results."""
import hashlib
from pathlib import Path

REQUIRED = {
    'configuration.json', 'launch.json', 'mapped.v', 'metrics.json',
    'native-final/native-structure.rpt', 'native-final/timing-intent.rpt',
    'native-final/clocks.rpt', 'native-final/area.rpt', 'native-final/setup.rpt',
    'native-final/hold.rpt', 'native-final/constraints.sdc',
    'native-final/hold-provenance.json', 'native-hold/provenance.json',
    'native-hold/hold.rpt', 'native-hold/worst-hold-slack.rpt',
    'native-hold/timing-coverage.rpt', 'native-hold/units.rpt',
    'native-hold/setup-crosscheck.rpt', 'native-hold/console.log',
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def seal(root):
    root = Path(root)
    for name in REQUIRED:
        if not (root/name).is_file():
            raise RuntimeError('Required synthesis evidence missing: '+name)
    paths = {root/name for name in REQUIRED}
    for folder in ('native-final', 'native-hold'):
        paths.update(p for p in (root/folder).rglob('*') if p.is_file())
    return {str(p.relative_to(root)): digest(p) for p in sorted(paths)}


def verify(root, status, allow_unsealed=False):
    if 'artifacts_sha256' not in status and 'evidence_schema_version' not in status:
        if allow_unsealed:
            return 'UNVERIFIED_LEGACY'
        raise ValueError('Legacy synthesis results have no evidence seal; use --allow-unsealed only for explicitly unverified historical inspection')
    artifacts = status.get('artifacts_sha256')
    if status.get('evidence_schema_version') != 1 or not isinstance(artifacts,dict) or not REQUIRED <= artifacts.keys():
        raise ValueError('Incomplete retained synthesis evidence seal')
    for name, expected in artifacts.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid retained artifact path')
        path = Path(root)/relative
        if not path.is_file() or digest(path) != expected:
            raise ValueError('Retained artifact changed: '+str(path))
    if digest(Path(root)/'mapped.v') != status.get('mapped_sha256'):
        raise ValueError('Mapped netlist differs from its completion record')
    return 'VERIFIED'
