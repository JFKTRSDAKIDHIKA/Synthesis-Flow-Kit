#!/usr/bin/env python3
"""Exact Liberty cell area by streamed module counts, without flattening RTL."""
import argparse
from collections import Counter
import math
from pathlib import Path
import re
from netlist_lib import INSTANCE_RE


def libraries(paths):
    cells={}
    for path in paths:
        name=None
        for line in Path(path).read_text().splitlines():
            match=re.match(r'\s*cell\s*\(\s*"?([^\s")]+)"?\s*\)',line)
            if match:
                name=match.group(1)
                if name in cells: raise ValueError('Duplicate Liberty cell '+name)
                cells[name]={'area':None,'sequential':False}
            elif name:
                match=re.match(r'\s*area\s*:\s*([0-9.eE+-]+)\s*;',line)
                if match:
                    if cells[name]['area'] is not None: raise ValueError('Duplicate cell area '+name)
                    area=float(match.group(1))
                    if not math.isfinite(area) or area<0: raise ValueError('Invalid cell area '+name)
                    cells[name]['area']=area
                if re.match(r'\s*(ff|latch|ff_bank|latch_bank)\s*\(',line): cells[name]['sequential']=True
    if any(c['area'] is None for c in cells.values()): raise ValueError('Missing Liberty cell area')
    return cells


def module_counts(path):
    modules={};name=None;in_instance=False
    with Path(path).open() as stream:
        for line in stream:
            line=line.rstrip('\n')
            if line.startswith('module '):
                name=line[len('module '):].split('(',1)[0].strip().lstrip('\\')
                if name in modules: raise ValueError('Duplicate module '+name)
                modules[name]=Counter();in_instance=False
            elif line.startswith('endmodule'): name=None;in_instance=False
            elif name:
                match=INSTANCE_RE.match(line)
                if match: modules[name][match.group(1).lstrip('\\')]+=1;in_instance=True
                elif line.startswith('  );'): in_instance=False
                elif not in_instance and line.strip() and not line.lstrip().startswith(('//','wire ','input ','output ','inout ','assign ','(*')):
                    # Module headers may span several lines. Mapped bodies have
                    # no behavioral statements or parameterized cells.
                    if re.match(r'\s*(always|initial|parameter|generate|function|task|\$)',line): raise ValueError('Unmapped construct '+line)
    if name is not None: raise ValueError('Unterminated module')
    return modules


def totals(modules,cells,top):
    memo={};active=set()
    def expand(name):
        if name in memo:return memo[name]
        if name in active:raise ValueError('Recursive module '+name)
        if name not in modules:raise ValueError('Missing module '+name)
        active.add(name);count=Counter()
        for kind,n in modules[name].items():
            if kind in cells:count[kind]+=n
            elif kind in modules:
                for cell,m in expand(kind).items():count[cell]+=n*m
            else:raise ValueError('Unresolved cell '+kind)
        active.remove(name);memo[name]=count;return count
    return expand(top)


def report(netlist,libs,top):
    cells=libraries(libs);mods=module_counts(netlist);counts=totals(mods,cells,top)
    def area(count):return math.fsum(n*cells[c]['area'] for c,n in count.items())
    text=['Exact hierarchical cell count against Liberty; complete fixed gate netlist.']
    for name,count in mods.items():
        own={c:n for c,n in count.items() if c in cells}
        text += [f"Chip area for module '\\{name}': {area(own):.6f}"]
    text += ['=== design hierarchy ===','Count including submodules.']
    text += [f'    {n} {n*cells[c]["area"]:.9f} {c}' for c,n in sorted(counts.items())]
    text += ['  0 submodules',f"Chip area for top module '\\{top}': {area(counts):.6f}",f"    of which used for sequential elements: {math.fsum(n*cells[c]['area'] for c,n in counts.items() if cells[c]['sequential']):.6f}"]
    return '\n'.join(text)+'\n'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--netlist',required=True);p.add_argument('--top',required=True);p.add_argument('--lib',action='append',required=True);p.add_argument('--out',required=True);a=p.parse_args();Path(a.out).write_text(report(a.netlist,a.lib,a.top))
