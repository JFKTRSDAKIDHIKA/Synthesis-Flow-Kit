"""Proof-gated extraction of the LSU's 128-entry register-table updates.

Eight banks of sixteen entries retain their original registers in the parent.
Only exclusive combinational cones move; shared logic is never duplicated.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from genus_lsu import run,sha,state_comparison

HELPERS=tuple('GenusLsuStateBank'+str(i) for i in range(8))

def split_cones(modules,name):
    m=modules[name];cells=m['cells']
    targets=[set() for _ in range(8)]
    flops={q:(c,i) for c in cells.values() if c['type'] in ('$adff','$dff') for i,q in enumerate(c['connections']['Q'])}
    entries=set();state_bits=0;seen=set()
    for alias,v in m['netnames'].items():
        match=re.search(r'(?:^|\.)state_cells_(\d+)_(\w+)$',alias)
        if not match:continue
        index=int(match.group(1));assert 0<=index<128
        entries.add(index)
        for q in v['bits']:
            if q not in flops or q in seen:continue
            seen.add(q);state_bits+=1
            c,i=flops[q];d=c['connections']['D'][i]
            if isinstance(d,int):targets[index//16].add(d)
    assert len(entries)==128
    print('register table',len(entries),'entries',state_bits,'state bits')
    def outputs(c):
     return {b for p,d in c['port_directions'].items() if d=='output' for b in c['connections'][p] if isinstance(b,int)}
    def inputs(c):
     return {b for p,d in c['port_directions'].items() if d=='input' for b in c['connections'][p] if isinstance(b,int)}
    def sequential(c):return not c['type'].startswith('$') or any(t in c['type'].lower() for t in ('ff','latch','mem'))
    producers={b:n for n,c in cells.items() for b in outputs(c)}
    stop={}
    for g,bits in enumerate(targets):
     for b in bits:stop[b]=stop.get(b,0)|(1<<g)
    owners={}
    for g,bits in enumerate(targets):
     todo=list(bits);seen=set()
     while todo:
      b=todo.pop()
      if b in stop and not stop[b]&(1<<g):continue
      n=producers.get(b)
      if n is None or n in seen or sequential(cells[n]):continue
      seen.add(n);owners[n]=owners.get(n,0)|(1<<g);todo.extend(inputs(cells[n]))
     print('cone',g,'cells',len(seen),flush=True)
    groups=[{n for n,v in owners.items() if v==(1<<g)} for g in range(8)]
    exclusive=set().union(*groups);assert sum(map(len,groups))==len(exclusive)
    parent={n:c for n,c in cells.items() if n not in exclusive}
    consumers={}
    for n,c in cells.items():
     for b in inputs(c):consumers.setdefault(b,set()).add(n)
    port_outputs={b for p in m['ports'].values() if p['direction']=='output' for b in p['bits'] if isinstance(b,int)}
    clock=m['ports']['clock']['bits'];assert len(clock)==1
    stats=[]
    for g,names in enumerate(groups):
     assert names,'empty byte group'
     produced=set().union(*(outputs(cells[n]) for n in names))
     used=set().union(*(inputs(cells[n])|outputs(cells[n]) for n in names))
     incoming=set().union(*(inputs(cells[n]) for n in names))-produced-set(clock)
     outgoing={b for b in produced if b in port_outputs or consumers.get(b,set())-names}
     assert outgoing,'unobservable byte group'
     ports={'clock':dict(direction='input',bits=clock),'sources':dict(direction='input',bits=sorted(incoming)),
            'results':dict(direction='output',bits=sorted(outgoing))}
     portbits=set().union(*(set(p['bits']) for p in ports.values()))
     netnames={n:dict(bits=p['bits'],hide_name=0,attributes={}) for n,p in ports.items()}
     internal=used-portbits
     covered=set()
     for n in sorted(names):
      for port,direction in cells[n]['port_directions'].items():
       if direction!='output':continue
       bits=[b for b in cells[n]['connections'][port] if b in internal and b not in covered]
       if bits:
        netnames['signal_'+str(len(netnames))]=dict(bits=bits,hide_name=0,attributes={})
        covered.update(bits)
     assert covered==internal, 'all internal bits must have a producer'
     helper='GenusLsuStateBank'+str(g)
     modules[helper]=dict(attributes={},ports=ports,cells={n:cells[n] for n in sorted(names)},netnames=netnames)
     parent['state_bank_'+str(g)]=dict(type=helper,parameters={},attributes={},
                                    port_directions={n:p['direction'] for n,p in ports.items()},
                                    connections={n:p['bits'] for n,p in ports.items()})
     stats.append(dict(group=g,cells=len(names),input_bits=len(incoming),output_bits=len(outgoing)))
    m['cells']=parent
    assert len(parent)-8+sum(x['cells'] for x in stats)==len(cells)
    return stats


def factor_state(data,yosys,out):
    modules=data['modules'];names=[n for n in modules if n=='LsuV3' or n.startswith('LsuV3$')]
    if len(names)!=1 or any(n in modules for n in HELPERS):
        raise ValueError('state-bank factoring requires one LSU and fresh helper names')
    if any('GenusLsuByteGroup'+str(i) not in modules for i in range(8)):
        raise ValueError('state-bank factoring requires proof-gated byte-group factoring first')
    name=names[0];closure={}
    def include(n):
        if n not in modules or n in closure:return
        closure[n]=modules[n]
        for c in modules[n].get('cells',{}).values():include(c['type'])
    include(name)
    source=json.dumps(dict(modules=closure),sort_keys=True)
    key=hashlib.sha256(source.encode()+Path(__file__).read_bytes()
        +Path(__file__).with_name('genus_lsu.py').read_bytes()
        +subprocess.check_output(yosys+['-V'])).hexdigest()
    folder=out/'lsu-state-factor'/key;folder.mkdir(parents=True,exist_ok=True)
    candidate=folder/'candidate.json';proof=folder/'proof.json';log=folder/'equivalence.log'
    cached=json.loads(proof.read_text()) if proof.exists() else {}
    if not (cached.get('key')==key and cached.get('status')=='passed'
        and candidate.exists() and cached.get('candidate_sha256')==sha(candidate)
        and log.exists() and cached.get('log_sha256')==sha(log)):
        original=folder/'original.json';original.write_text(source)
        rewritten=json.loads(source);stats=split_cones(rewritten['modules'],name)
        candidate.write_text(json.dumps(rewritten));del rewritten,source,closure
        q=lambda p:json.dumps(str(p))
        script=folder/'flatten.ys';lines=[]
        for label,path in [('gold',original),('gate',candidate)]:
            lines += [f'read_json {q(path)}',f'hierarchy -check -top {name}',
                'flatten','opt_clean -purge',f'write_json {q(folder/(label+".json"))}','design -reset']
        script.write_text('\n'.join(lines)+'\n');run(yosys,script,folder/'flatten.log')
        gold=json.loads((folder/'gold.json').read_text())['modules'][name]
        gate=json.loads((folder/'gate.json').read_text())['modules'][name]
        comparison,bits=state_comparison(gold,gate)
        if bits!=14828:raise ValueError('unsupported LSU state geometry: '+str(bits))
        cut=folder/'state-comparison.json';cut.write_text(json.dumps(comparison))
        del comparison,gold,gate
        script=folder/'equivalence.ys'
        script.write_text(f'read_json {q(cut)}\nequiv_make gold gate equiv\n'
            'hierarchy -top equiv\nopt_expr\nwreduce\nopt_merge\nopt_clean\n'
            'techmap\nopt_expr\nopt_merge\nopt_clean\n'
            'equiv_simple -short -nogroup\nequiv_status -assert\n')
        run(yosys,script,log)
        proof.write_text(json.dumps(dict(status='passed',key=key,register_bits=bits,
            candidate_sha256=sha(candidate),log_sha256=sha(log),groups=stats,
            source_composition_sha256=sha(original),entries=128,entries_per_bank=16,
            relation='bijective register state; identical outputs, next state, reset and clock; no duplicated primitive cells; original registers stay in parent'),indent=2)+'\n')
    rewritten=json.loads(candidate.read_text())['modules'];modules[name]=rewritten[name]
    for helper in HELPERS:modules[helper]=rewritten[helper]
    print('PASS: eight LSU state-table banks proved; all registers retained',flush=True)
    return HELPERS
