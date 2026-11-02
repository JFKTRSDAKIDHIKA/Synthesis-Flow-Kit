#!/usr/bin/env python3
"""Bind the approved AMEOOO 256-bit SRAM organization without editing source RTL.

Preserves both 2048-bit MRF read replicas and all address/control/data wiring.
Models are exploratory ASAP7-derived estimates, not characterized N6 SRAM IP.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from prepare_ameooo_asap7 import definition


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bind(rtl, models, out):
    identity = json.loads((models / 'model-manifest.json').read_text())
    assert identity['model_id'] == 'ameooo-asap7-derived-sram-estimates-wide-v5'
    assert identity['organization'] == {'mrf_width': 256, 'tcm_slice_width': 256,
                                        'mrf_read_replicas': 2, 'mrf_bits_per_replica_word': 2048}
    for rel, digest in identity['output_sha256'].items():
        assert sha(models / rel) == digest, rel
    configs = {cfg['ports'] + ':' + str(cfg['depth']): cfg for cfg in identity['macros']}
    assert set(configs) == {'1rw:128', '1rw:2048', '1r1w:1024'}
    assert all(cfg['width'] == 256 for cfg in configs.values())
    assert [configs[k]['instances'] for k in ['1rw:128', '1rw:2048', '1r1w:1024']] == [2, 2, 16]
    original = rtl.read_text()
    source_hash = sha(rtl)
    patches, wrappers, references, memories = [], [], [], []
    for depth in [128, 2048]:
        module = f'memory_{depth}x512'
        cfg = configs[f'1rw:{depth}']
        start, header, end = definition(original, module)
        references.append(original[start:end].replace('module '+module+'(', 'module ref_'+module+'(', 1))
        lines = [original[start:header], '  // Exploratory 256-bit SRAM estimate; not native N6 IP.']
        for i in range(2):
            lines += [f"  {cfg['name']} u_slice_{i} (",
                      '    .clk0(RW0_clk), .csb0(~RW0_en), .web0(~RW0_wmode),',
                      f'    .wmask0(RW0_wmask[{32*i} +: 32]), .addr0(RW0_addr),',
                      f'    .din0(RW0_wdata[{256*i} +: 256]), .dout0(RW0_rdata[{256*i} +: 256])', '  );']
        lines.append('endmodule')
        wrapper = '\n'.join(lines)
        patches.append((start, end, wrapper))
        wrappers.append(wrapper)
        memories.append(module)
    leaf = 'readDataFromEachBank_bankMemoryForReadPort_768x64'
    start, _, end = definition(original, 'AmeRegisterFile')
    parent = original[start:end]
    matches = list(re.finditer(r'^  '+leaf+r'\s+(\w+)\s*\((.*?)^  \);', parent, re.M | re.S))
    assert len(matches) == 64, 'Expected exactly 32 slices per read replica'
    replicas = {}
    for match in matches:
        ports = dict(re.findall(r'\.(\w+)\s*\(([^()]*)\)', match[2]))
        assert set(ports) == {'R0_addr','R0_en','R0_clk','R0_data','W0_addr','W0_en','W0_clk','W0_data'}
        ports = {k:v.strip() for k,v in ports.items()}
        data = re.fullmatch(r'io_writeRequestToMrf_bits_data\[(\d+):(\d+)\]', ports['W0_data'])
        assert data and int(data[1]) == int(data[2]) + 63 and int(data[2]) % 64 == 0
        replicas.setdefault(ports['R0_addr'], []).append((int(data[2])//64, match, ports))
    assert len(replicas) == 2
    certificates = []
    controls = ['R0_addr','R0_en','R0_clk','W0_addr','W0_en','W0_clk']
    outputs = set()
    for replica, (address, cells) in enumerate(replicas.items()):
        cells.sort(key=lambda x:x[0])
        assert [x[0] for x in cells] == list(range(32))
        control = {p:cells[0][2][p] for p in controls}
        assert all({p:x[2][p] for p in controls} == control for x in cells)
        for group in range(8):
            chunk = cells[group*4:group*4+4]
            for _, _, ports in chunk:
                assert re.fullmatch(r'\w+', ports['R0_data'])
                assert ports['R0_data'] not in outputs
                outputs.add(ports['R0_data'])
            actual = dict(control)
            for p in ['W0_data','R0_data']:
                actual[p] = '{' + ', '.join(x[2][p] for x in reversed(chunk)) + '}'
            instance = f'coral_mrf_r{replica}_slice_{group}'
            call = '  coral_mrf_768x256_1r1w '+instance+' (\n'+',\n'.join(
                '    .'+p+' ('+actual[p]+')' for p in ['R0_addr','R0_en','R0_clk','R0_data','W0_addr','W0_en','W0_clk','W0_data'])+'\n  );'
            for pos, (_, match, _) in enumerate(chunk):
                patches.append((start+match.start(), start+match.end(), call if pos == 0 else ''))
            certificates.append({'instance':instance,'replica':replica,'word_bits':[group*256+255,group*256],
                                 'original_instances':[x[1][1] for x in chunk], 'controls':control,
                                 'read_lanes_lsb_first':[x[2]['R0_data'] for x in chunk],
                                 'write_lanes_lsb_first':[x[2]['W0_data'] for x in chunk]})
    cfg = configs['1r1w:1024']
    grouped = '''module coral_mrf_768x256_1r1w(
  input [9:0] R0_addr, input R0_en, R0_clk, output [255:0] R0_data,
  input [9:0] W0_addr, input W0_en, W0_clk, input [255:0] W0_data
);
  // Same logical 768-word range and independent read/write clocks.
  %s u_memory (
    .clk0(W0_clk), .csb0(~(W0_en && (W0_addr < 10'd768))),
    .addr0(W0_addr), .din0(W0_data),
    .clk1(R0_clk), .csb1(~(R0_en && (R0_addr < 10'd768))),
    .addr1(R0_addr), .dout1(R0_data)
  );
endmodule''' % cfg['name']
    wrappers.append(grouped)
    patches.sort()
    assert all(a[1] <= b[0] for a,b in zip(patches, patches[1:]))
    mapped, cursor, inverse_patches = [], 0, []
    offset = 0
    for a,z,new in patches:
        untouched = original[cursor:a]
        mapped.extend([untouched,new])
        offset += len(untouched)
        inverse_patches.append({'offset':offset,'new':new,'old':original[a:z]})
        offset += len(new)
        cursor = z
    mapped.append(original[cursor:])
    append = '\n\n'+grouped+'\n'
    mapped = ''.join(mapped)+append
    inverse = mapped[:-len(append)]
    for p in reversed(inverse_patches):
        assert inverse[p['offset']:p['offset']+len(p['new'])] == p['new']
        inverse = inverse[:p['offset']]+p['old']+inverse[p['offset']+len(p['new']):]
    assert inverse == original
    out.mkdir(parents=True, exist_ok=False)
    for folder in ['models','lib']:
        shutil.copytree(models/folder,out/folder)
    (out/'blackboxes.v').write_text('\n'.join((models/'blackboxes'/(cfg['name']+'.v')).read_text() for cfg in identity['macros']))
    (out/'wrappers.sv').write_text('\n\n'.join(wrappers)+'\n')
    (out/'reference-memories.sv').write_text('\n\n'.join(references)+'\n')
    (out/'AMEOOO.asap7-est.sv').write_text(mapped)
    macros = [{k:cfg[k] for k in ['name','ports','depth','logical_depth','width','instances']} for cfg in identity['macros']]
    spec = {'top':'AMEOOO','macros':macros,'macro_instances':20,
            'logical_macro_bits':identity['logical_bytes']*8,'physical_macro_bits':identity['physical_model_bytes']*8,
            'unused_macro_bits':identity['unused_padding_bytes']*8}
    assert spec['logical_macro_bits'] == 4259840 and spec['physical_macro_bits'] == 5308416
    (out/'macro-spec.json').write_text(json.dumps(spec,indent=2)+'\n')
    (out/'grouping-certificate.json').write_text(json.dumps({'status':'passed','groups':certificates,
        'source_read_lanes':len(outputs),'same_controls_per_replica':True,'full_source_inverse_reconstruction':'passed',
        'source_sha256':source_hash,'patches':inverse_patches},indent=2)+'\n')
    assert sha(rtl) == source_hash
    evidence = {'status':'bound_pending_interface_qualification','model_manifest':str(models/'model-manifest.json'),
        'model_manifest_sha256':sha(models/'model-manifest.json'),'source_rtl':str(rtl),'source_rtl_sha256':source_hash,
        'changed_modules_only':memories+['AmeRegisterFile'],'added_module':'coral_mrf_768x256_1r1w',
        'source_rtl_unchanged':True,'macro_instances':20,'synthesis_launched':False,
        'scope':'Exploratory ASAP7-based 256-bit models; no native N6 SRAM characterization; capacity, ports, clocks and interface cycles retained.',
        'generated_sha256':{str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}}
    (out/'binding-manifest.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl',required=True,type=Path)
    parser.add_argument('--models',required=True,type=Path)
    parser.add_argument('--out-dir',required=True,type=Path)
    args = parser.parse_args()
    print(json.dumps(bind(args.rtl.resolve(), args.models.resolve(), args.out_dir.resolve()),indent=2))
