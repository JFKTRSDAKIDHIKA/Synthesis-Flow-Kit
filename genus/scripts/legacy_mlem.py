"""Replay a frozen MLEM synthesis contract through the canonical entry only.

Historical clock/library choices are not new platform defaults. The Tcl is
an explicitly hash-pinned input from the preserved campaign; this adapter does
not generate a new MLEM hardware configuration or run Innovus.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import time
from resource_runtime import execute, available, ResourceRetry


def replay(manifest, genus, sha, write_json, entry):
    manifest=Path(manifest).resolve()
    cfg=json.loads(manifest.read_text());root=Path(cfg['campaign']).resolve()
    pins=json.loads((root/'input-lock.json').read_text())
    script=root/'scripts/mlem-n6-synth.tcl'
    if str(script) not in pins or pins[str(script)]!=sha(script):
        raise ValueError('Frozen MLEM synthesis Tcl must be explicitly included in input-lock.json')
    for name,digest in pins.items():
        if sha(name)!=digest:raise ValueError('Frozen MLEM input changed: '+name)
    marker=root/'reports/synth.complete.json'
    if marker.exists():raise ValueError('Synthesis already exists; preserve the completed campaign')
    tool=shutil.which(genus)
    if not tool:raise ValueError('Genus executable not found')
    if not os.environ.get('CDS_LIC_FILE'):raise ValueError('Source the workspace environment (CDS_LIC_FILE required)')
    if not cfg.get('genus_lock'):raise ValueError('An explicit shared genus_lock is required in the legacy manifest')
    lock=Path(cfg['genus_lock']);lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+') as slot:
        try:fcntl.flock(slot,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ResourceRetry('Shared Genus slot is occupied')
        if available()<36*(1<<30):raise ResourceRetry('Insufficient host RAM for frozen MLEM replay')
        record=root/'reports/canonical-genus-replay'
        record.mkdir(parents=True,exist_ok=False)
        command=[tool,'-no_gui','-batch','-files',str(script)]
        write_json(record/'launch.json',dict(entry=str(entry),entry_sha256=sha(entry),manifest=str(manifest),
            manifest_sha256=sha(manifest),inputs_sha256=pins,command=command,mode='frozen-historical-MLEM-synthesis',
            resource_policy='36GiB admission,8/12GiB pause/resume,32GiB sampled PSS cap',
            scope='Historical native synthesis replay; no timing/physical signoff claim'))
        logfile=record/'console.log'
        env=dict(MLEM_N6_CAMPAIGN=str(root),MLEM_N6_STAGE='synth',CORAL_GENUS_ENTRY=str(entry),
                 LM_LICENSE_FILE='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        execute(command,root/'synthesis',logfile,lambda m:write_json(record/'resources.json',m),env)
        text=logfile.read_text(errors='replace')
        if not any(line.strip()=='MLEM_N6_SYNTH_COMPLETE' for line in text.splitlines()) or re.search(r'\*\*ERROR:|^Error\s*:|MLEM_N6_FAILED',text,re.M):
            raise RuntimeError('Frozen MLEM replay failed native completion/error checks')
        netlist=root/'synthesis/mlem_core.v'
        if not netlist.exists() or netlist.stat().st_size<1000:raise RuntimeError('Missing synthesized MLEM netlist')
        for name,digest in pins.items():
            if sha(name)!=digest:raise RuntimeError('Frozen input changed during replay: '+name)
        result=dict(stage='synth',status='completed',finished_epoch=time.time(),netlist_sha256=sha(netlist),
                    canonical_entry=str(entry),launch_record=str(record/'launch.json'))
        write_json(marker,result)
        write_json(record/'result.json',result)
        print(marker)
