"""Proof-gated generic LSU cleanup; Genus retains all technology mapping.

Compare every external output and state transition with arbitrary common
register contents. A bijection of register bits and equal reset values/clock
polarities proves the sequential relation, including asynchronous reset.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE=Path(__file__).resolve().parent
CLEAN="proc\nopt_expr\nopt_merge\nwreduce\nopt_clean -purge\n"

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(yosys,script,log):
    with log.open('w') as stream:
        subprocess.run(yosys+['-s',str(script)],stdout=stream,stderr=subprocess.STDOUT,check=True)

def state_comparison(gold,gate):
    assert {n:(p['direction'],len(p['bits'])) for n,p in gold['ports'].items()} == {n:(p['direction'],len(p['bits'])) for n,p in gate['ports'].items()}, 'external interface changed'
    def flops(m):
     result={}
     for n,c in m['cells'].items():
      if c['type'] in ('$adff','$dff'):
       for i,q in enumerate(c['connections']['Q']):
        assert isinstance(q,int) and q not in result
        result[q]=(n,c,i)
      elif c['type'].startswith(('$mem','$dlatch','$sdff','$adffe','$dffe')):raise RuntimeError(c['type'])
     return result
    fq,fqg=flops(gold),flops(gate)
    links={q:set() for q in fq}
    for n,v in gold['netnames'].items():
     if n not in gate['netnames'] or re.search(r'(?:^|\.)_[0-9]+_$',n):continue
     w=gate['netnames'][n]
     if len(v['bits'])!=len(w['bits']):continue
     for b,bg in zip(v['bits'],w['bits']):
      if b in links and bg in fqg:links[b].add(bg)
    missing=[q for q,v in links.items() if len(v)!=1]
    print('register bits',len(fq),len(fqg),'unmatched/ambiguous',len(missing),flush=True)
    assigned={next(iter(v)) for v in links.values() if len(v)==1}
    remaining=set(fqg)-assigned
    if len(missing)==len(remaining)==1 and not links[missing[0]]:
     links[missing[0]]=remaining
     print('unique residual state pairing; verified by reset, next-state and output proof',flush=True)
    else:assert not missing,missing[:20]
    links={q:next(iter(v)) for q,v in links.items()}
    assert len(links)==len(fqg) and set(links.values())==set(fqg), 'state mapping must be bijective'
    def flag(c,k):return int(c['parameters'][k],2)
    def resetbit(c,i):return c['parameters']['ARST_VALUE'][-i-1]
    observed={'gold':{},'gate':{}}
    for j,(q,qg) in enumerate(sorted(links.items())):
     _,c,i=fq[q];_,cg,ig=fqg[qg]
     assert c['type']==cg['type']
     assert flag(c,'CLK_POLARITY')==flag(cg,'CLK_POLARITY')
     if c['type']=='$adff':
      assert flag(c,'ARST_POLARITY')==flag(cg,'ARST_POLARITY')
      assert resetbit(c,i)==resetbit(cg,ig)
     for name,cell,idx in [('gold',c,i),('gate',cg,ig)]:
      observed[name]['proof_next_'+str(j)]=[cell['connections']['D'][idx]]
      observed[name]['proof_clock_'+str(j)]=cell['connections']['CLK']
      if cell['type']=='$adff':observed[name]['proof_reset_'+str(j)]=cell['connections']['ARST']
    for name,m,states in [('gold',gold,links),('gate',gate,{q:q for q in fqg})]:
     allbits=[b for p in m['ports'].values() for b in p['bits']]+[b for c in m['cells'].values() for bits in c['connections'].values() for b in bits]
     fresh=max(b for b in allbits if isinstance(b,int))+1
     inputs={qg:fresh+i for i,qg in enumerate(sorted(fqg))}
     replace={q:inputs[qg] for q,qg in states.items()}
     def bits(v):return [replace.get(b,b) for b in v]
     m['cells']={n:c for n,c in m['cells'].items() if c['type'] not in ('$adff','$dff')}
     for c in m['cells'].values():c['connections']={n:bits(v) for n,v in c['connections'].items()}
     for p in m['ports'].values():p['bits']=bits(p['bits'])
     for qg,b in inputs.items():m['ports']['proof_state_'+str(qg)]=dict(direction='input',bits=[b])
     for n,v in observed[name].items():m['ports'][n]=dict(direction='output',bits=bits(v))
     m['netnames']={n:dict(hide_name=0,bits=p['bits'],attributes={}) for n,p in m['ports'].items()}
     m['attributes']={}

    return dict(modules=dict(gold=gold,gate=gate)), len(fq)

def clean_lsu(rtl,top,yosys,out):
    out.mkdir(parents=True,exist_ok=True)
    version=subprocess.check_output(yosys+['-V'],text=True).strip()
    key=hashlib.sha256((sha(rtl)+sha(__file__)+CLEAN+version).encode()).hexdigest()
    folder=out/key;folder.mkdir(exist_ok=True)
    original=folder/'original.v';candidate=folder/'cleaned.v'
    proof=folder/'proof.json';log=folder/'equivalence.log'
    if proof.exists():
        record=json.loads(proof.read_text())
        if (record['key']==key and record['status']=='passed' and candidate.exists()
            and sha(candidate)==record['cleaned_rtl_sha256'] and log.exists()
            and sha(log)==record['proof_log_sha256']):
            rtl.write_bytes(candidate.read_bytes());return record
    original.write_bytes(rtl.read_bytes())
    script=folder/'clean.ys'
    script.write_text(f'read_verilog -sv {json.dumps(str(original))}\nhierarchy -check -top {top}\n'+CLEAN+f'write_verilog -noattr {json.dumps(str(candidate))}\n')
    run(yosys,script,folder/'clean.log')
    script=folder/'states.ys';lines=[]
    for path,name in [(original,'gold'),(candidate,'gate')]:
        lines += [f'read_verilog -sv {json.dumps(str(path))}', f'hierarchy -check -top {top}',
                  'proc','flatten','opt_clean -purge',f'rename {top} {name}',
                  f'write_json {json.dumps(str(folder/(name+".json")))}',f'design -stash {name}']
    script.write_text('\n'.join(lines)+'\n');run(yosys,script,folder/'states.log')
    gold=json.loads((folder/'gold.json').read_text())['modules']['gold']
    gate=json.loads((folder/'gate.json').read_text())['modules']['gate']
    comparison,state_bits=state_comparison(gold,gate)
    cut=folder/'state-comparison.json';cut.write_text(json.dumps(comparison));del comparison,gold,gate
    script=folder/'equivalence.ys'
    script.write_text(f'read_json {json.dumps(str(cut))}\nequiv_make gold gate equiv\nhierarchy -top equiv\nopt_expr\nwreduce\nopt_merge\nopt_clean\ntechmap\nopt_expr\nopt_merge\nopt_clean\nequiv_simple -short -nogroup\nequiv_status -assert\n')
    run(yosys,script,log)
    record=dict(status='passed',key=key,original_rtl_sha256=sha(original),cleaned_rtl_sha256=sha(candidate),
                proof_log_sha256=sha(log),register_bits=state_bits,
                relation='bijective arbitrary register state; all external outputs, next-state and reset/clock functions; identical reset values and polarities')
    proof.write_text(json.dumps(record,indent=2)+'\n')
    rtl.write_bytes(candidate.read_bytes())
    return record
