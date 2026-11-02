#!/usr/bin/env python3
"""Independent, cached bottom-up Genus mapping of fully elaborated RTL blocks.

Slang/Yosys performs elaboration, generic RTL lowering and optional expression
cleanup. Genus performs native optimization and all technology mapping. Child gate netlists are read using
Cadence's read_hdl -netlist and preserve=true, never empty timing blackboxes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import threading
from genus_schedule import schedule
from netlist_lib import parse_modules
from genus_mt import factor_matrix, prove_mapped_helper, HELPERS, RTL as MATRIX_FACTOR_RTL
from genus_lsu import clean_lsu
from genus_lsu_factor import factor_lsu, HELPERS as LSU_HELPERS
from genus_cec import prove_combinational_helper
from genus_cec_x import prove_defined_helper
from genus_lsu_state import factor_state, HELPERS as LSU_STATE_HELPERS

HERE = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def quote(value):
    return json.dumps(str(value))

def mapping_identity(identity, version=2, legacy_driver=False):
    # The prepared unit RTL captures elaboration/lowering changes. Keep all
    # native mapping/validation inputs in its cache key; orchestration-only
    # changes need not force completed, identical units to be synthesized again.
    result=dict(identity)
    if version >= 2:
        result.pop('blocks',None)
        result.pop('jobs',None)
        result.pop('flatten_blocks',None)
        result.pop('clean_blocks',None)
        result.pop('factor_mt',None)
        result.pop('factor_lsu',None)
        result.pop('factor_lsu_state',None)
    preprocessing={(HERE/'genus_cone_extract.py').resolve(), (HERE/'genus_schedule.py').resolve(), (HERE/'genus_blocks.py').resolve(), (HERE/'genus_mt.py').resolve(), (HERE/'genus_lsu.py').resolve(), (HERE/'genus_lsu_factor.py').resolve(), (HERE/'genus_cec.py').resolve(), (HERE/'genus_cec_x.py').resolve(), (HERE/'genus_cec_parts.py').resolve(), (HERE/'genus_lsu_state.py').resolve(), MATRIX_FACTOR_RTL.resolve()}
    result['hashes']={k:v for k,v in identity['hashes'].items()
                      if Path(k).resolve() not in preprocessing}
    if not legacy_driver:
        driver=str((HERE/'run_genus.py').resolve())
        # This exact older driver differs only below the native Genus call.
        # Its archived source and unchanged native prefix prove the migration.
        old='1a507ae585ac22b46c4ed73c67e201908d88195f92341569faf89de51b5501a9'
        prefix='ab4e40b624acc64a39f19fedbba02d7e3128f71da9f3d8280276de37ed64cf6b'
        if result['hashes'].get(driver)==old:
            result['hashes'][driver]=prefix
        elif result['hashes'].get(driver)==sha(HERE/'run_genus.py'):
            source=(HERE/'run_genus.py').read_text()
            result['hashes'][driver]=hashlib.sha256(source.split('    libraries = [',1)[0].encode()).hexdigest()
    result['unit_cache_format']='prepared-rtl-v'+str(version)
    return result

def check_prepared_rtl(path):
    unresolved=set(re.findall(r'^\s*\\(\$\S+)\s+(?:#\s*\(|[\w$]+\s*\()',
                             Path(path).read_text(),re.M))
    if unresolved:
        raise ValueError('Unlowered Yosys cells cannot be read by Genus: '+str(sorted(unresolved)))

def run(argv, logfile):
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open('w') as f:
        subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, check=True)

def module_chunks(path):
    text=Path(path).read_text()
    return {m.group(1).lstrip('\\'):m.group(0) for m in re.finditer(r'^module\s+(\\\S+|[\w$]+)\s*\(.*?^endmodule\b[^\n]*', text, re.M|re.S)}

def build_plan(data, top, blocks):
    mods=data['modules']
    if top not in mods: raise ValueError('missing elaborated top')
    reachable=set()
    def visit(n):
        if n in reachable or n not in mods: return
        reachable.add(n)
        for cell in mods[n].get('cells',{}).values(): visit(cell['type'])
    visit(top)
    selected={n for n in reachable if n==top or any(n==b or n.startswith(b+'$') for b in blocks)}
    missing=[b for b in blocks if not any(n==b or n.startswith(b+'$') for n in selected)]
    if missing: raise ValueError('blocks absent after elaboration: '+str(missing))
    names={n:(top if n==top else n.split('$')[0]+'__g'+hashlib.sha256(n.encode()).hexdigest()[:10]) for n in selected}
    children={}
    def first_blocks(n, active):
        if n in active: raise ValueError('recursive RTL hierarchy')
        result=set()
        for cell in mods[n].get('cells',{}).values():
            t=cell['type']
            if t in selected: result.add(t)
            elif t in mods and not mods[t].get('attributes',{}).get('blackbox'): result |= first_blocks(t,active|{n})
        return result
    for n in selected: children[n]=first_blocks(n,set())
    order=[];seen=set()
    def post(n):
        if n in seen:return
        seen.add(n)
        for t in sorted(children[n]):post(t)
        order.append(n)
    post(top)
    units=[]
    for n in order:
        ports=mods[n]['ports']
        clocks=[p for p in ('io_aclk','clock','clk','io_clk','clk_i','io_clock','RW0_clk') if p in ports and ports[p]['direction']=='input' and len(ports[p]['bits'])==1]
        if len(clocks) > 1 or (not clocks and n == top):
            raise ValueError('ambiguous block clock: '+n+' '+str(clocks))
        if not clocks:
            def has_state(name, visited):
                if name in visited: return False
                visited.add(name)
                for cell in mods[name].get('cells', {}).values():
                    kind = cell['type']
                    if kind.startswith('$') and re.search(r'ff|latch|mem', kind): return True
                    if kind in mods and has_state(kind, visited): return True
                return False
            if has_state(n, set()):
                raise ValueError('sequential block has no recognized clock: '+n)
        units.append(dict(unit=names[n],rtl_top=n,clock=clocks[0] if clocks else '__virtual_clock__',top=n==top,ties={},children=[names[t] for t in sorted(children[n])],blackboxes=[]))
    return dict(top=top,units=units,blocks=[dict(block=names[n],specializations=[dict(unit=names[n],ties={},sites=[])]) for n in sorted(selected) if n!=top],retype=[],renames=names)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('rtl','blackboxes','std-lib','sram-lib-dir','spec','out-dir','netlist'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--top',required=True);p.add_argument('--blocks',required=True)
    p.add_argument('--yosys-boundaries',action='store_true',help='use Yosys literal-tie specialization and its exact block boundaries')
    p.add_argument('--genvar-repair-proof',type=Path,help='Explicit qualified proof for an already-repaired generic snapshot')
    p.add_argument('--repair-rvv-genvars', action='store_true', help='Apply certified nonparameterized RVV boundary genvar metadata repair')
    p.add_argument('--flatten-blocks',default='',help='collapse internal RTL helpers only inside these selected units')
    p.add_argument('--clean-blocks',default='',help='simplify generic expressions and wire widths inside these selected units before native mapping')
    p.add_argument('--extract-element-cones',action='store_true',help='preserve exact generic cells while extracting element result-bank and issue-lane cones')
    p.add_argument('--plan-only',action='store_true',help='Stop after fresh elaboration and checked structural boundary extraction; no native mapping')
    p.add_argument('--resume-offline-prepared',action='store_true',help='use sealed checked generic plan and exact offline-prepared unit RTL')
    p.add_argument('--reuse-from',type=Path,help='checked reference-only reuse from a preserved previous output directory')
    p.add_argument('--factor-mt',action='store_true',help='prove and factor VME matrix read ports and write banks into reusable units')
    p.add_argument('--factor-lsu',action='store_true',help='prove and split LSU byte-transfer logic into eight combinational units')
    p.add_argument('--factor-lsu-state',action='store_true',help='prove and split the 128-entry LSU register-table update logic into eight banks')
    p.add_argument('--yosys',required=True);p.add_argument('--genus',required=True)
    p.add_argument('--jobs',type=int,default=1);p.add_argument('--threads',type=int,default=8);p.add_argument('--period-ps',type=int,default=2500)
    p.add_argument('--effort',default='medium');p.add_argument('--defines',default='SYNTHESIS USE_FAKERAM')
    a=p.parse_args()
    if a.jobs<1:p.error('--jobs must be positive')
    if a.reuse_from:
        import importlib.util
        origin_script=a.reuse_from.resolve()/'flow-source/scripts/genus_blocks.py'
        if not origin_script.is_file():
            origin_script=a.reuse_from.resolve().parent/'synthesis-flow/scripts/genus_blocks.py'
        if not origin_script.is_file():
            raise ValueError('Reuse requires the preserved origin flow source, not just mapped outputs')
        origin_identity=json.loads((a.reuse_from/'identity.json').read_text())
        if origin_identity['hashes'].get(str(origin_script)) != sha(origin_script):
            raise ValueError('Preserved origin driver changed')
        origin_spec=importlib.util.spec_from_file_location('preserved_origin_genus_blocks',origin_script)
        origin_flow=importlib.util.module_from_spec(origin_spec);origin_spec.loader.exec_module(origin_flow)
    out=a.out_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    inputs=[a.rtl,a.blackboxes,a.std_lib,a.spec,HERE/'genus_blocks.py',HERE/'genus_schedule.py',HERE/'run_genus.py',HERE.parent/'tcl/genus-block.tcl',HERE.parent/'tcl/stage-driver.tcl']+sorted((HERE.parent/'tcl/stages').glob('*.tcl'))+sorted(a.sram_lib_dir.glob('*.lib'))
    if a.repair_rvv_genvars: inputs += [HERE/'repair_genvars.py'] + ([a.genvar_repair_proof] if a.genvar_repair_proof else [])
    if a.extract_element_cones: inputs += [HERE/'genus_cone_extract.py']
    if a.yosys_boundaries: inputs += [HERE/'genus_yosys_plan.py',HERE/'block_plan.py']
    if a.factor_mt: inputs += [HERE/'genus_mt.py',MATRIX_FACTOR_RTL]
    if 'LsuV3' in a.clean_blocks.split(): inputs += [HERE/'genus_lsu.py']
    if a.factor_lsu: inputs += [HERE/'genus_lsu_factor.py',HERE/'genus_lsu.py',HERE/'genus_cec.py']
    if a.factor_lsu_state: inputs += [HERE/'genus_lsu_state.py',HERE/'genus_cec_x.py',HERE/'genus_cec_parts.py']
    if a.factor_lsu_state and not a.factor_lsu: p.error('--factor-lsu-state requires --factor-lsu')
    identity=dict(jobs=a.jobs,hashes={str(x.resolve()):sha(x) for x in inputs},blocks=a.blocks,flatten_blocks=a.flatten_blocks,clean_blocks=a.clean_blocks,factor_mt=a.factor_mt,factor_lsu=a.factor_lsu,factor_lsu_state=a.factor_lsu_state,top=a.top,threads=a.threads,period=a.period_ps,effort=a.effort,defines=a.defines,yosys=a.yosys,genus=a.genus)
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    previous_identity=json.loads((out/'identity.json').read_text()) if (out/'identity.json').is_file() else None
    previous_fingerprint=hashlib.sha256(json.dumps(previous_identity,sort_keys=True).encode()).hexdigest() if previous_identity else None
    native_identity=mapping_identity(identity)
    (out/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    yosys=shlex.split(a.yosys)
    offline_manifest=None
    if a.resume_offline_prepared:
        assert previous_identity is not None and mapping_identity(previous_identity)==native_identity, 'Offline plan mapping inputs changed'
        offline_manifest=json.loads((out/'offline-preparation-manifest.json').read_text())
        assert offline_manifest['status']=='pass', 'Offline preparation incomplete'
        named=out/'named.json';planpath=out/'plan.json'
        assert sha(named)==offline_manifest['source_sha256'] and sha(planpath)==offline_manifest['plan_sha256'], 'Offline generic snapshot changed'
        proof=json.loads((out/'functional-extraction/structural-equivalence.json').read_text())
        assert proof['status']=='pass' and all(x['before_cell_sha256']==x['expanded_after_cell_sha256'] for x in proof['reports'])
        plan=json.loads(planpath.read_text())
        assert set(offline_manifest['units'])=={u['unit'] for u in plan['units']}
        a.clean_blocks += ' '+' '.join(u['rtl_top'] for u in plan['units'] if u['rtl_top'].startswith(('AmeElementResultBank_','AmeElementIssueLanes_','AmeLsuResultBeatSegment_','AmeLsuStagingBankWrite_')))
        assembly=dict(plan,units=sorted(plan['units'],key=lambda u:not u['top']))
        assembly_path=out/'assembly-plan.json';assembly_path.write_text(json.dumps(assembly,indent=2)+'\n')
        print('Verified sealed generic plan and all offline-prepared RTL; no technology mapping substitution',flush=True)
    else:
        lowered=out/'elaborated.json';stamp=out/'elaborated.sha256'
        yosys=shlex.split(a.yosys)
        if not a.yosys_boundaries and (not lowered.is_file() or not stamp.is_file() or stamp.read_text().strip()!=fingerprint):
            script=out/'elaborate.ys'
            defs=' '.join('-D'+x for x in a.defines.split())
            script.write_text(f'read_slang --best-effort-hierarchy --empty-blackboxes --top {a.top} {defs} {shlex.quote(str(a.rtl.resolve()))} {shlex.quote(str(a.blackboxes.resolve()))}\nhierarchy -check -top {a.top}\nproc\nwrite_json {quote(lowered)}\n')
            print('Genus blocks: elaborating the exact RTL once (no Yosys technology mapping)',flush=True)
            run(yosys+['-m','slang','-s',str(script)],out/'elaborate.log')
            stamp.write_text(fingerprint+'\n')
        data=json.loads(lowered.read_text()) if not a.yosys_boundaries else {};blocks=a.blocks.split()
        if a.yosys_boundaries:
            if a.reuse_from:
                origin_identity=json.loads((a.reuse_from/'identity.json').read_text())
                for field in ('top','blocks','defines','yosys'):
                    assert origin_identity[field]==identity[field], 'Generic snapshot input mismatch: '+field
                required=[a.rtl.resolve(),a.blackboxes.resolve(),HERE/'genus_yosys_plan.py',HERE/'block_plan.py']
                for path in required:
                    matches=[v for k,v in origin_identity['hashes'].items() if Path(k).name==path.name]
                    assert matches==[sha(path)], 'Generic snapshot source/planner mismatch: '+str(path)
                origin_plan=json.loads((a.reuse_from/'plan.json').read_text())
                inverse={u['unit']:u['rtl_top'] for u in origin_plan['units']}
                snapshot=a.reuse_from/'named.json'
                print('Loading verified frozen generic snapshot; no source re-elaboration or mapping substitution',flush=True)
                data=json.loads(snapshot.read_text())
                for mod in data['modules'].values():
                    for cell in mod.get('cells',{}).values():cell['type']=inverse.get(cell['type'],cell['type'])
                data['modules']={inverse.get(n,n):m for n,m in data['modules'].items()}
                blocks=[u['rtl_top'] for u in origin_plan['units'] if not u['top']]
                (out/'generic-source-reuse.json').write_text(json.dumps(dict(origin=str(snapshot),sha256=sha(snapshot),source_and_planner_hashes='pass',source_selection='identical',role='Generic elaboration snapshot only; all technology mapping remains native Genus'),indent=2)+'\n')
            else:
                from genus_yosys_plan import prepare
                data,blocks,source_plan=prepare(a.rtl.resolve(),a.blackboxes.resolve(),a.top,blocks,a.defines,yosys,out/'yosys-boundaries')
        if a.repair_rvv_genvars:
            import importlib.util
            repair_path = HERE / 'repair_genvars.py'
            repair_spec = importlib.util.spec_from_file_location('repair_genvars', repair_path)
            repair_module = importlib.util.module_from_spec(repair_spec)
            repair_spec.loader.exec_module(repair_module)
            repair_module.repair(data, a.rtl, out / 'genvar-repair-proof.json', a.genvar_repair_proof)
        if a.factor_mt:
            blocks += [n for n in factor_matrix(data,yosys,out) if n not in blocks]
        if a.factor_lsu:
            blocks += [n for n in factor_lsu(data,yosys,out) if n not in blocks]
        if a.factor_lsu_state:
            blocks += [n for n in factor_state(data,yosys,out) if n not in blocks]
        if a.extract_element_cones:
            from genus_cone_extract import factor
            extracted_helpers=factor(data,out/'functional-extraction')
            blocks += extracted_helpers
            a.clean_blocks += ' '+' '.join(x for x in extracted_helpers if x.startswith(('AmeElementResultBank_','AmeElementIssueLanes_','AmeLsuResultBeatSegment_','AmeLsuStagingBankWrite_')))
        plan=build_plan(data,a.top,blocks)
        for module in data['modules'].values():
            for cell in module.get('cells',{}).values():
                cell['type']=plan['renames'].get(cell['type'],cell['type'])
        data['modules']={plan['renames'].get(n,n):m for n,m in data['modules'].items()}
        named=out/'named.json';named.write_text(json.dumps(data));del data
        planpath=out/'plan.json';planpath.write_text(json.dumps(plan,indent=2)+'\n')
        # The assembler wants top first; synthesis itself proceeds leaf first.
        assembly=dict(plan,units=sorted(plan['units'],key=lambda u:not u['top']))
        assembly_path=out/'assembly-plan.json';assembly_path.write_text(json.dumps(assembly,indent=2)+'\n')
    if a.plan_only:
        print('PASS fresh generic plan ready; no native mapping launched',flush=True)
        return
    prep_lock=threading.Lock()
    print(f"Genus blocks: {len(plan['units'])} units; up to {a.jobs} simultaneous units, {a.threads} threads each",flush=True)
    renames=''
    def execute(unit,completed):
        name=unit['unit'];folder=out/'units'/name;folder.mkdir(parents=True,exist_ok=True)
        target=folder/'netlist'/f'{name}.v';done=folder/'done.json'
        children=[]
        def include(n):
            if n in children:return
            for c in completed[n]['children']:include(c)
            children.append(n)
        for n in unit['children']:include(n)
        depfiles=[completed[n]['netlist'] for n in children]
        all_modules={n for path in depfiles for n in module_chunks(path)}
        cached=json.loads(done.read_text()) if done.is_file() else {}
        start=time.time();rtl=folder/'unit.v';script=folder/'prepare.ys'
        previous_rtl_sha=sha(rtl) if rtl.is_file() else None
        # Reprepare even a cached unit: reuse is based on the actual RTL Genus
        # reads, not a guess that changes in the orchestration are harmless.
        # bwmuxmap is generic RTL lowering only: no Liberty/ABC mapping.
        helper_flatten='flatten\nopt_clean -purge\n' if unit['rtl_top'].split('$')[0] in a.flatten_blocks.split() else ''
        base=unit['rtl_top'].split('$')[0]
        generic_cleanup='bmuxmap\ndemuxmap\nopt_expr\nwreduce\nopt_clean -purge\n' if base in a.clean_blocks.split() and base!='LsuV3' else ''
        script.write_text(f'read_json {quote(named)}\n'+renames+''.join(f'blackbox {n}\n' for n in children)+f'hierarchy -check -top {name}\nbwmuxmap\n'+helper_flatten+generic_cleanup+f'write_verilog -noattr {quote(rtl)}\n')
        if offline_manifest is not None:
            import shutil
            prepared=offline_manifest['units'][name];source=Path(prepared['rtl'])
            assert sha(source)==prepared['rtl_sha256']
            assert sha(source.parent/'input.json')==prepared['input_sha256'] and sha(source.parent/'prepare.ys')==prepared['script_sha256']
            shutil.copy2(source,rtl)
            shutil.copy2(source.parent/'prepare.log',folder/'prepare.log')
            script.write_text('# Exact checked offline preparation source; output copied with SHA256 validation\n'+(source.parent/'prepare.ys').read_text())
        else:
            with prep_lock:
                run(yosys+['-s',str(script)],folder/'prepare.log')
        if base=='LsuV3' and base in a.clean_blocks.split() and not a.factor_lsu:
            clean_lsu(rtl,name,yosys,folder/'rtl-cleanup')
        check_prepared_rtl(rtl)
        rtl_sha=sha(rtl)
        dependencies=[sha(x) for x in depfiles]
        key=hashlib.sha256(json.dumps(dict(identity=native_identity,unit=name,rtl_sha256=rtl_sha,children=dependencies),sort_keys=True).encode()).hexdigest()
        if a.reuse_from and not done.is_file():
            origin=a.reuse_from.resolve()/'units'/name
            origin_done_path=origin/'done.json'
            if origin_done_path.is_file():
                origin_done=json.loads(origin_done_path.read_text())
                origin_identity=json.loads((a.reuse_from/'identity.json').read_text())
                old_native=origin_flow.mapping_identity(origin_identity)
                portable=lambda ident:dict(ident,hashes={Path(k).name:v for k,v in ident['hashes'].items()})
                origin_target=origin/'netlist'/f'{name}.v'
                origin_key=hashlib.sha256(json.dumps(dict(identity=old_native,unit=name,rtl_sha256=sha(origin/'unit.v'),children=dependencies),sort_keys=True).encode()).hexdigest()
                if (portable(old_native)==portable(native_identity)
                    and sha(origin/'unit.v')==rtl_sha
                    and origin_done.get('key')==origin_key
                    and origin_done.get('sha256')==sha(origin_target)):
                    import shutil
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(origin_target,target)
                    cached=dict(origin_done,key=key,rtl_sha256=rtl_sha,
                                reused_from=str(origin),reuse_validation='Exact prepared RTL, native mapping inputs, original cache key, dependency hashes and mapped netlist SHA256 match')
                    done.write_text(json.dumps(cached,indent=2)+'\n')
                    print('Reusing checked native result '+name,flush=True)
        legacy_key=hashlib.sha256((previous_fingerprint+name+''.join(dependencies)).encode()).hexdigest() if previous_fingerprint else None
        previous_unit_key=hashlib.sha256(json.dumps(dict(identity=mapping_identity(previous_identity,version=1),unit=name,rtl_sha256=previous_rtl_sha,children=dependencies),sort_keys=True).encode()).hexdigest() if previous_identity else None
        previous_v2_key=hashlib.sha256(json.dumps(dict(identity=mapping_identity(previous_identity,legacy_driver=True),unit=name,rtl_sha256=previous_rtl_sha,children=dependencies),sort_keys=True).encode()).hexdigest() if previous_identity else None
        legacy_valid=(previous_identity is not None and cached.get('key') in (legacy_key,previous_unit_key,previous_v2_key)
                      and previous_rtl_sha==rtl_sha
                      and mapping_identity(previous_identity)==native_identity)
        migration_origin=previous_fingerprint
        # An imported legacy entry belongs to its own recorded synthesis
        # identity, not the most recent identity of the destination folder.
        if not legacy_valid and cached.get('cache_origin'):
            origin=Path(cached['cache_origin'])
            origin_identity_path=origin.parent.parent/'identity.json'
            origin_done_path=origin/'done.json'
            origin_rtl=origin/'unit.v';origin_target=origin/'netlist'/f'{name}.v'
            if all(p.is_file() for p in (origin_identity_path,origin_done_path,origin_rtl,origin_target)):
                origin_identity=json.loads(origin_identity_path.read_text())
                origin_done=json.loads(origin_done_path.read_text())
                origin_fingerprint=hashlib.sha256(json.dumps(origin_identity,sort_keys=True).encode()).hexdigest()
                origin_global_key=hashlib.sha256((origin_fingerprint+name+''.join(dependencies)).encode()).hexdigest()
                origin_unit_key=hashlib.sha256(json.dumps(dict(identity=mapping_identity(origin_identity,version=1),unit=name,rtl_sha256=rtl_sha,children=dependencies),sort_keys=True).encode()).hexdigest()
                legacy_valid=(previous_rtl_sha==rtl_sha and sha(origin_rtl)==rtl_sha
                              and origin_done.get('key')==cached.get('key')
                              and cached.get('key') in (origin_global_key,origin_unit_key)
                              and origin_done.get('sha256')==cached.get('sha256')==sha(origin_target)
                              and mapping_identity(origin_identity)==native_identity)
                if legacy_valid:migration_origin=origin_fingerprint
        if (cached.get('key')==key or legacy_valid) and target.is_file() and cached.get('sha256')==sha(target):
            if legacy_valid:
                cached.update(key=key,rtl_sha256=rtl_sha,migrated_from_identity_sha256=migration_origin)
                done.write_text(json.dumps(cached,indent=2)+'\n')
            print('Cached '+name,flush=True)
        else:
            mapped=folder/'combined.v'
            cmd=[sys.executable,str(HERE/'run_genus.py'),'--genus',a.genus,'--yosys',a.yosys,'--rtl',str(rtl),'--blackboxes',str(a.blackboxes.resolve()),'--top',name,'--clk-port',unit['clock'],'--std-lib',str(a.std_lib.resolve()),'--sram-lib-dir',str(a.sram_lib_dir.resolve()),'--spec',str(a.spec.resolve()),'--out-dir',str(folder),'--netlist',str(mapped),'--script',str(HERE.parent/'tcl/genus-block.tcl'),'--threads',str(a.threads),'--period-ps',str(a.period_ps),'--effort',a.effort,'--defines',a.defines,'--hierarchy','preserve','--unit-mode','--mode','io']
            for path in depfiles:cmd+=['--child-netlist',str(path)]
            for child in children:cmd+=['--preserve-block',child]
            print('Mapping '+name,flush=True);run(cmd,folder/'driver.log')
            chunks=module_chunks(mapped)
            owned=[n for n in chunks if n not in all_modules]
            # Give private internal modules unique names across unit runs.
            private={n:name+'__internal_'+hashlib.sha256(n.encode()).hexdigest()[:10] for n in owned if n!=name}
            libraries=[a.std_lib.resolve()]+sorted(a.sram_lib_dir.resolve().glob('*.lib'))
            normalize=folder/'normalize.ys'
            normalize.write_text(''.join(f'read_liberty -lib {quote(x)}\n' for x in libraries)+''.join(f'read_verilog -sv -lib {quote(x)}\n' for x in depfiles)+f'read_verilog -sv {quote(mapped)}\nhierarchy -check -top {name}\n'+''.join(f'rename {n} {new}\nchtype -set {new} t:{n}\n' for n,new in private.items())+f'write_verilog -noattr -noexpr -nohex -nodec {quote(folder/"namespaced.v")}\n')
            run(yosys+['-s',str(normalize)],folder/'normalize.log')
            chunks=module_chunks(folder/'namespaced.v')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text('\n\n'.join(text for n,text in chunks.items() if n not in all_modules)+'\n')
            done.write_text(json.dumps(dict(key=key,rtl_sha256=rtl_sha,sha256=sha(target),elapsed_seconds=time.time()-start),indent=2)+'\n')
            print(f'Mapped {name} in {time.time()-start:.0f} s',flush=True)
        if a.factor_mt and unit['rtl_top'] in HELPERS:
            prove_mapped_helper(name,rtl,target,a.std_lib.resolve(),yosys,folder)
        if a.factor_lsu and unit['rtl_top'] in LSU_HELPERS:
            prove_combinational_helper(name,rtl,target,a.std_lib.resolve(),yosys,folder)
        if a.factor_lsu_state and unit['rtl_top'] in LSU_STATE_HELPERS:
            prove_defined_helper(name,rtl,target,a.std_lib.resolve(),yosys,folder)
        return dict(children=unit['children'],netlist=target)
    exclusive={u['unit'] for u in plan['units'] if u['top'] or u['rtl_top'].split('$')[0] in ('RvvCoreWrapper','LsuV3','SCore')}
    schedule(plan['units'],execute,a.jobs,exclusive)
    run([sys.executable,str(HERE/'assemble_netlist.py'),'--plan',str(assembly_path),'--units-dir',str(out/'units'),'--top',a.top,'--std-lib',str(a.std_lib.resolve()),'--sram-lib-dir',str(a.sram_lib_dir.resolve()),'--spec',str(a.spec.resolve()),'--out',str(a.netlist.resolve()),'--clock-port','io_aclk','--blocks-json',str(out/'blocks.json'),'--report',str(out/'blocks.txt')],out/'assembly.log')
    print('PASS: independently mapped Genus blocks assembled, interfaces/clocks/SRAM counts checked',flush=True)

if __name__ == '__main__':
    if not os.environ.get('CORAL_GENUS_ENTRY'):
        raise SystemExit('Use Synthesis-Flow-Kit/genus/run.py; this is an internal backend.')
    main()
