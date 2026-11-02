#!/usr/bin/env python3
"""Prepare read-only ASAP7 inputs for native-scale research implementation."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def group_end(text, start):
    depth = 0
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*|[{}]', re.S)
    for match in tokens.finditer(text, start):
        token = match[0]
        if token == '{':
            depth += 1
        elif token == '}':
            depth -= 1
            if depth == 0:
                return match.end()
    raise ValueError('Unclosed Liberty group')


def merge_libraries(paths, output):
    """Namespace templates, retaining each original cell and table value."""
    headers, cells, names = [], [], set()
    attributes = None
    for index, path in enumerate(paths):
        text = path.read_text()
        first = re.search(r'(?m)^\s{2}cell\s*\(', text)
        library = re.search(r'\blibrary\s*\([^)]*\)\s*\{', text)
        if not first or not library:
            raise ValueError('Unsupported library layout: ' + str(path))
        header = text[library.end():first.start()]
        prefix = 'source%d__' % index
        groups = list(re.finditer(r'(?m)^  (\w+)\s*\(([^)]+)\)\s*\{', header))
        rename = {m[2].strip().strip('"'): prefix + m[2].strip().strip('"') for m in groups}
        pattern = re.compile(r'\b(' + '|'.join(re.escape(k) for k in sorted(rename, key=len, reverse=True)) + r')\b')
        replace = lambda value: pattern.sub(lambda m: rename[m[0]], value)
        remainder, cursor = [], 0
        for match in groups:
            end = group_end(header, header.index('{', match.start()))
            remainder.append(header[cursor:match.start()])
            headers.append(replace(header[match.start():end]))
            cursor = end
        remainder.append(header[cursor:])
        if attributes is None:
            attributes = replace(''.join(remainder))
        for match in re.finditer(r'(?m)^  cell\s*\(([^)]+)\)\s*\{', text):
            name = match[1].strip().strip('"')
            if name in names:
                raise ValueError('Duplicate cell: ' + name)
            names.add(name)
            end = group_end(text, text.index('{', match.start()))
            cells.append(replace(text[match.start():end]))
    output.write_text('library (asap7_rvt_tt_merged) {\n' + attributes + '\n' + '\n'.join(headers + cells) + '\n}\n')
    return len(names)


def prepare_view(original, output):
    text = original.read_text()
    derived = text
    removed = [('LAYER', 'V9'), ('LAYER', 'Pad'), ('VIA', 'VIA9Pad'), ('VIARULE', 'Pad_M9')]
    for kind, name in removed:
        derived, count = re.subn(rf'(?ms)^{kind} {name}\b.*?^END {name}[ \t]*$', '', derived)
        if count != 1:
            raise ValueError('Unexpected technology block count: ' + name)
    for name in [f'M{i}' for i in range(1, 10)] + [f'V{i}' for i in range(9)]:
        pattern = rf'(?ms)^LAYER {name}\b.*?^END {name}[ \t]*$'
        if re.search(pattern, text)[0] != re.search(pattern, derived)[0]:
            raise ValueError('Used layer changed: ' + name)
    output.write_text(derived)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdk-root', required=True, type=Path)
    parser.add_argument('--lib-dir', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    libs = sorted(args.lib_dir.glob('*RVT_TT*nldm*.lib'))
    if len(libs) != 5:
        raise ValueError('Expected five ASAP7 RVT TT NLDM library files')
    merged = args.out_dir / 'asap7-rvt-tt.lib'
    cells = merge_libraries(libs, merged)
    original = args.pdk_root / 'techlef_misc/asap7_tech_1x_201209.lef'
    view = args.out_dir / 'signal-stack.lef'
    removed = prepare_view(original, view)
    mapping = [('Gate', 'gate')] + [(f'M{i}', f'm{i}') for i in range(1, 10)] + [(f'V{i}', f'v{i}') for i in range(9)]
    continuation = ' ' + chr(92) + '\n'
    lines = ['extraction_setup', '    -technology_layer_map'] + ['    %s %s' % pair for pair in mapping]
    (args.out_dir / 'rc-layer.map').write_text(continuation.join(lines) + '\n')
    record = {'inputs_sha256': {str(p.resolve()): sha(p) for p in libs + [original]},
              'merged_liberty_sha256': sha(merged), 'merged_cells': cells,
              'derived_lef_sha256': sha(view), 'removed_blocks': removed,
              'used_layer_rules_identical': True, 'coordinate_scale': 1,
              'time_unit_ps': 1, 'capacitance_unit_ff': 1,
              'scope': 'Predictive M1-M9 core implementation; no pad-ring or foundry signoff'}
    (args.out_dir / 'provenance.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'output_dir': str(args.out_dir), 'cells': cells}, indent=2))


if __name__ == '__main__':
    main()
