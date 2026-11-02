"""Run and preserve real min-delay STA independently of Genus setup reports."""
from pathlib import Path
import re
import shlex


def analyze(out, config, base, snapshot, executable, execute, update, write_json, sha):
    folder=out/'native-hold';folder.mkdir()
    settings=config.get('hold_sta',{})
    std=(base/settings.get('std_lib',config['std_lib'])).resolve()
    macros=(base/settings.get('sram_lib_dir',config['sram_lib_dir'])).resolve()
    sdc=out/'native-final/constraints.sdc'
    env=dict(FLOW_TOP=config['top'],FLOW_OUT=str(folder),FLOW_RTL=str(out/'mapped.v'),FLOW_SDC=str(sdc),
             FLOW_STD_LIB=str(std),FLOW_SRAM_LIB_DIR=str(macros),FLOW_PERIOD_PS=str(config['options']['period_ps']),
             OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    command=shlex.split(executable)+['-exit',str(snapshot/'tcl/report-opensta-hold.tcl')]
    inputs=[std,out/'mapped.v',sdc,*sorted(macros.glob('*.lib'))]
    provenance=dict(schema_version=1,engine='OpenSTA',command=command,
                    corner_id=settings.get('corner_id','same-as-synthesis'),
                    input_sha256={str(p):sha(p) for p in inputs},clock_mode='ideal',parasitics='none',
                    scope='Pre-layout min-delay/hold STA at the recorded Liberty corner, ideal clocks and no extracted interconnect; not post-CTS or multi-corner hold signoff')
    write_json(folder/'provenance.json',provenance)
    execute(command,folder,folder/'console.log',update,env)
    text=(folder/'console.log').read_text(errors='replace')
    if 'CORAL_OPENSTA_HOLD_OK' not in text.splitlines() or re.search(r'^Error\b|HOLD_STA_FAILED',text,re.M):
        raise RuntimeError('OpenSTA hold analysis failed; inspect '+str(folder/'console.log'))
    for name in ('hold.rpt','worst-hold-slack.rpt','timing-coverage.rpt','units.rpt','setup-crosscheck.rpt'):
        if not (folder/name).is_file() or (name in {'hold.rpt','worst-hold-slack.rpt'} and not (folder/name).stat().st_size):
            raise RuntimeError('Hold STA report missing: '+name)
    for path,digest in provenance['input_sha256'].items():
        if sha(path)!=digest: raise RuntimeError('Hold STA input changed: '+path)
    provenance['engine_version']=next((line.strip() for line in text.splitlines() if line.startswith('OpenSTA ')), 'unreported')
    provenance['state']='complete'
    write_json(folder/'provenance.json',provenance)
    # Keep the discoverable final report with explicit engine provenance.
    (out/'native-final/hold.rpt').write_text('Engine: OpenSTA\n'+provenance['scope']+'\n\n'+(folder/'hold.rpt').read_text())
    write_json(out/'native-final/hold-provenance.json',provenance)
    return provenance
