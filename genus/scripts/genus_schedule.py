"""Dependency-aware bounded jobs, with exclusive admission for large units."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def schedule(units, execute, jobs=1, exclusive=()):
    if jobs<1: raise ValueError('jobs must be positive')
    pending=list(units);completed={};running={}
    names={u['unit'] for u in units}
    if len(names)!=len(units):raise ValueError('duplicate unit')
    for u in units:
        if not set(u['children'])<=names:raise ValueError('missing dependency')
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while pending or running:
            for u in list(pending):
                if len(running)>=jobs:break
                if not set(u['children'])<=completed.keys():continue
                large=u['unit'] in exclusive
                if (large and running) or any(v['unit'] in exclusive for v in running.values()):continue
                snapshot=dict(completed)
                running[pool.submit(execute,u,snapshot)]=u;pending.remove(u)
                if large:break
            if not running:raise ValueError('cyclic dependencies')
            finished,_=wait(running,return_when=FIRST_COMPLETED)
            for future in finished:
                u=running.pop(future)
                completed[u['unit']]=future.result()
    return completed
