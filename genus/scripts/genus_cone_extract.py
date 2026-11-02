"""Exact cell/bit connectivity-preserving functional extraction; no logic rewrite."""
import collections
import copy
import hashlib
import json
import re
from pathlib import Path


def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def factor(data, out):
    reports=[]; helpers=[]
    for parent,m in list(data["modules"].items()):
        if parent not in ("AmeElementUnit","AmeReductionUnit","AmeLsu"):
            continue
        original_cells=copy.deepcopy(m["cells"])
        original_ports=copy.deepcopy(m["ports"])
        cells=m["cells"]
        drivers={}
        for n,c in cells.items():
            for p,bs in c["connections"].items():
                if c["port_directions"][p]=="output":
                    for b in bs:
                        if isinstance(b,int):
                            assert b not in drivers or drivers[b]==n, (parent,b)
                            drivers[b]=n
        def sequential(c):
            return bool(re.search(r"ff|latch|mem",c["type"]))
        def upstream(seeds,allow_state=False):
            selected=set();todo=list(seeds)
            while todo:
                n=todo.pop()
                if n in selected:continue
                c=cells[n]
                if not c["type"].startswith("$"):continue
                if sequential(c) and (not allow_state or n not in seeds):continue
                selected.add(n)
                for p,bs in c["connections"].items():
                    if c["port_directions"][p]=="input":
                        todo.extend(drivers[b] for b in bs if b in drivers)
            return selected
        if parent=='AmeElementUnit':
            groups=collections.defaultdict(set)
            for n,c in cells.items():
                match=re.fullmatch(r"\$driver\$resultElementsForEachContext_(\d+)_(\d+)",n)
                if match:
                    context,index=map(int,match.groups())
                    group=f"AmeElementResultBank_indices_b{index:03}"
                    groups[group]|=upstream({n},True)
            assert sum(1 for n in cells if n.startswith("$driver$resultElementsForEachContext_"))==256
            for n,c in cells.items():
                match=re.fullmatch(r"requestBufferForArithmeticUnit_(\d+)_2",n)
                if match:
                    lane=int(match.group(1))
                    seeds={drivers[b] for p,bs in c["connections"].items() if c["port_directions"][p]=="input" for b in bs if b in drivers}
                    groups[f"AmeElementIssueLanes_b{lane//8:02}"]|=upstream(seeds)
            assert len(groups)==136, list(groups)
        elif parent=='AmeReductionUnit':
            groups=collections.defaultdict(set)
            prefix=0;partial=0;operand=0
            for n,c in cells.items():
                match=re.fullmatch(r"\$driver\$prefixResultElementsForEachContext_([01])_(\d+)_(\d+)",n)
                if match:
                    context,group,index=map(int,match.groups());prefix+=1
                    groups[f"AmeReductionPrefixBank_g{group}_b{index//4:02}"]|=upstream({n},True)
                match=re.fullmatch(r"\$driver\$partialResultsForEachContext_([01])_(\d+)",n)
                if match:
                    context,index=map(int,match.groups());partial+=1
                    groups[f"AmeReductionPartialBank_b{index//4:02}"]|=upstream({n},True)
                match=re.fullmatch(r"\$driver\$(?:operandMicrotilesForEachContext|operandBufferIsValidForEachContext)_([01])_(\d+)",n)
                if match:
                    context,group=map(int,match.groups());operand+=1
                    groups[f"AmeReductionOperandBuffer_g{group}"]|=upstream({n},True)
            assert (prefix,partial,operand)==(512,64,16),(prefix,partial,operand)
            assert len(groups)==76,len(groups)
        elif parent=='AmeLsu':
            groups=collections.defaultdict(set)
            bits=cells["resultQueue"]["connections"]["io_enq_bits_data"]
            assert len(bits)==2048
            for base in range(0,2048,256):
                seeds={drivers[b] for b in bits[base:base+256] if b in drivers}
                groups[f"AmeLsuResultBeatSegment_s{base//256:02}"]|=upstream(seeds)
            for bank in range(16):
                memory=cells["bankData_memory"+("_"+str(bank) if bank else "")+"_ext"]
                bits=memory["connections"]["RW0_wdata"]+memory["connections"]["RW0_wmask"]
                assert len(bits)==576
                seeds={drivers[b] for b in bits if b in drivers}
                groups[f"AmeLsuStagingBankWrite_b{bank:02}"]|=upstream(seeds)
            assert len(groups)==24,len(groups)
        memberships=collections.defaultdict(list)
        for g,ns in groups.items():
            for n in ns:memberships[n].append(g)
        # Shared control/decode remains in the original parent; every cell moves at most once.
        owners={n:gs[0] for n,gs in memberships.items() if len(gs)==1}
        # Keep response index decoders with their functional index bank even
        # when the legacy bank or parent also consumes that decoded bit.
        # Export shared decoder outputs; no logic is copied or rewritten.
        for cell,gs in memberships.items():
            index_groups=[g for g in gs if (parent=="AmeElementUnit" and g.startswith("AmeElementResultBank_indices_")) or (parent=="AmeReductionUnit" and g.startswith("AmeReductionPrefixBank_"))]
            if len(index_groups)==1:owners[cell]=index_groups[0]
        groups={g:{n for n in ns if owners.get(n)==g} for g,ns in groups.items()}
        external_sinks=collections.defaultdict(set)
        for n,c in cells.items():
            for p,bs in c["connections"].items():
                if c["port_directions"][p]=="input":
                    for b in bs:
                        if isinstance(b,int):external_sinks[b].add(owners.get(n,"parent"))
        for p,v in m["ports"].items():
            if v["direction"]=="output":
                for b in v["bits"]:
                    if isinstance(b,int):external_sinks[b].add("parent")
        reconstructed={n:c for n,c in cells.items() if n not in owners}
        group_reports=[]
        clockbits=m["netnames"]["clock"]["bits"]
        for g,ns in sorted(groups.items()):
            print(f"Extracting {g}: {len(ns)} original generic cells",flush=True)
            assert ns,g
            moved={n:cells[n] for n in ns}
            ins=set();outs=set();used=set()
            for n,c in moved.items():
                for p,bs in c["connections"].items():
                    for b in bs:
                        if not isinstance(b,int):continue
                        used.add(b)
                        if c["port_directions"][p]=="input" and drivers.get(b) not in ns:ins.add(b)
                        if c["port_directions"][p]=="output" and external_sinks[b]-{g}:outs.add(b)
            assert not ins&outs
            ports={};connections={};netnames={}
            for direction,boundary in [("input",ins),("output",outs)]:
                pending=set(boundary)
                preferred=sorted(m["netnames"].items(),key=lambda x:(x[0]!="clock",x[0].startswith("$"),len(x[1]["bits"]),x[0]))
                for name,v in preferred:
                    bits=v["bits"]
                    if not bits or len(set(bits))!=len(bits) or not set(bits)<=pending:continue
                    # Retain exact existing named buses, with ordinary stable port names.
                    port=name if name=="clock" else ("i_" if direction=="input" else "o_")+str(len(ports))
                    ports[port]={"direction":direction,"bits":bits}
                    connections[port]=bits
                    netnames[port]={"hide_name":0,"bits":bits,"attributes":{}}
                    pending-=set(bits)
                # Anonymous boundary bits are grouped in bounded consecutive runs.
                while pending:
                    first=min(pending);bits=[first];pending.remove(first)
                    while len(bits)<128 and bits[-1]+1 in pending:
                        bits.append(bits[-1]+1);pending.remove(bits[-1])
                    port=("i_" if direction=="input" else "o_")+str(len(ports))
                    ports[port]={"direction":direction,"bits":bits};connections[port]=bits
                    netnames[port]={"hide_name":0,"bits":bits,"attributes":{}}
            for name,v in m["netnames"].items():
                if name not in netnames and any(b in used for b in v["bits"] if isinstance(b,int)):
                    netnames[name]=dict(v,attributes={k:v for k,v in v.get("attributes",{}).items() if k!="init"})
            # No initialization/state attribute may be discarded silently.
            assert not any("init" in v.get("attributes",{}) for v in m["netnames"].values())
            child={"attributes":{},"ports":ports,"cells":moved,"netnames":netnames}
            data["modules"][g]=child;helpers.append(g)
            instance={"hide_name":0,"type":g,"parameters":{},"attributes":{},"port_directions":{p:v["direction"] for p,v in ports.items()},"connections":connections}
            m["cells"]["extract_"+g]=instance
            # Structural proof expands each new instance using its exact cut-port map.
            aliases={b:connections[p][i] for p,v in ports.items() for i,b in enumerate(v["bits"])}
            assert all(aliases[b]==b for b in ins|outs)
            for n,c in moved.items():
                assert n not in reconstructed
                restored=copy.deepcopy(c)
                restored["connections"]={p:[aliases.get(b,b) for b in bs] for p,bs in c["connections"].items()}
                assert restored==original_cells[n]
                reconstructed[n]=restored
            group_reports.append({"module":g,"cells":len(ns),"input_bits":len(ins),"output_bits":len(outs),"ports":len(ports),"clock_preserved":not any(sequential(c) for c in moved.values()) or ports.get("clock",{}).get("bits")==clockbits})
            assert group_reports[-1]["clock_preserved"]
        m["cells"]={n:c for n,c in m["cells"].items() if n not in owners}
        assert reconstructed==original_cells
        assert m["ports"]==original_ports
        reports.append({"parent":parent,"status":"pass","method":"Exact structural expansion of every extracted instance, preserving original cell names, types, parameters and all connection bits; original parent ports unchanged; cuts cover every crossed net and native clock.","before_cell_sha256":digest(original_cells),"expanded_after_cell_sha256":digest(reconstructed),"original_cells":len(original_cells),"remaining_parent_cells":len(m["cells"]),"moved_cells":len(owners),"groups":group_reports})
    assert len(reports)==3,"Expected all three frozen functional modules"
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/"structural-equivalence.json").write_text(json.dumps({"status":"pass","reports":reports},indent=2)+"\n")
    return helpers
