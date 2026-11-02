"""Extract conservative native metrics and compare two preserved synthesis runs."""
import json
from pathlib import Path
import re

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'


def timing(text):
    # Native final reports are explicitly emitted in picoseconds.
    values = [float(x) for x in re.findall(r'Slack\s*:=\s*('+NUMBER+')',text)]
    if not values:
        values = [float(x) for x in re.findall(r'Path\s+\d+:\s+\w+\s*\(('+NUMBER+')\s+ps\)',text)]
    if not values:
        values = [float(x) for x in re.findall(r'('+NUMBER+r')\s+slack\s+\((?:MET|VIOLATED)\)',text)]
    return min(values) if values else None


def collect(out, top):
    out = Path(out)
    def read(name):
        p = out/'native-final'/name
        return p.read_text(errors='replace') if p.is_file() else ''
    area = None; cells = None
    # Select the explicit top row; never sum hierarchical rows (double counting).
    row = re.search(r'^'+re.escape(top)+r'\s+\S+\s+(\d+)\s+('+NUMBER+')\s+('+NUMBER+')\s+('+NUMBER+r')\b',read('area.rpt'),re.M)
    if row: cells,area = int(row[1]),float(row[2])
    return dict(schema_version=1, worst_reported_setup_slack_ps=timing(read('setup.rpt')),
                worst_reported_hold_slack_ps=timing(read('hold.rpt')),cell_area=area,cell_count=cells,
                area_unit='Liberty area unit; confirm library convention before interpreting as um^2',
                timing_scope='Worst slack in retained native reports; no routed closure or equivalence claim',
                hold_engine='OpenSTA' if (out/'native-final/hold-provenance.json').is_file() else None,
                reports={name:str(out/'native-final'/name) for name in ('setup.rpt','hold.rpt','area.rpt')})


def hardware_identity(config, launch):
    base = Path(launch['configuration']).parent
    def pin(value):
        return launch['inputs_sha256'].get(str((base/value).resolve()))
    hold=config.get('hold_sta',{})
    hold_macro_dir=(base/hold.get('sram_lib_dir',config['sram_lib_dir'])).resolve()
    return dict(platform_id=config['platform_id'],top=config['top'],flow=config['flow'],
                inputs={key:pin(config[key]) for key in ('rtl','blackboxes','std_lib','spec')},
                macros=sorted(value for path,value in launch['inputs_sha256'].items()
                              if Path(path).parent==(base/config['sram_lib_dir']).resolve()),
                hold_corner=dict(corner_id=hold.get('corner_id','same-as-synthesis'),
                                 std_lib=pin(hold.get('std_lib',config['std_lib'])),
                                 macros=sorted(value for path,value in launch['inputs_sha256'].items() if Path(path).parent==hold_macro_dir)),
                tools=sorted(launch['tools_sha256'].values()))


def compare(baseline, variant, allow_different=False):
    rows=[]
    for run in (baseline,variant):
        run=Path(run).resolve()
        status=json.loads((run/'entry-status.json').read_text())
        if status.get('state')!='complete' or status.get('stage')!='synth':
            raise ValueError('Comparison requires completed synthesis: '+str(run))
        config=json.loads((run/'configuration.json').read_text())
        launch=json.loads((run/'launch.json').read_text())
        rows.append(dict(path=str(run),configuration=config,launch=launch,metrics=collect(run,config['top'])))
    same=hardware_identity(rows[0]['configuration'],rows[0]['launch'])==hardware_identity(rows[1]['configuration'],rows[1]['launch'])
    if not same and not allow_different:
        raise ValueError('Different hardware/platform/library/tool inputs; use --allow-different-inputs for an explicitly labelled comparison')
    keys=('worst_reported_setup_slack_ps','worst_reported_hold_slack_ps','cell_area','cell_count')
    metrics={key:dict(baseline=rows[0]['metrics'][key],variant=rows[1]['metrics'][key],
                     delta=(rows[1]['metrics'][key]-rows[0]['metrics'][key]
                            if all(row['metrics'][key] is not None for row in rows) else None)) for key in keys}
    changes={key:dict(baseline=rows[0]['configuration'].get(key),variant=rows[1]['configuration'].get(key))
             for key in set(rows[0]['configuration'])|set(rows[1]['configuration'])
             if key not in {'out_dir','lock_file','admission_gib'} and rows[0]['configuration'].get(key)!=rows[1]['configuration'].get(key)}
    return dict(schema_version=1,baseline=rows[0]['path'],variant=rows[1]['path'],same_hardware_inputs=same,
                metrics=metrics,configuration_changes=changes,
                input_hash_changes={path:dict(baseline=rows[0]['launch']['inputs_sha256'].get(path),variant=rows[1]['launch']['inputs_sha256'].get(path))
                                    for path in sorted(set(rows[0]['launch']['inputs_sha256'])|set(rows[1]['launch']['inputs_sha256']))
                                    if rows[0]['launch']['inputs_sha256'].get(path)!=rows[1]['launch']['inputs_sha256'].get(path)},
                scope=rows[0]['metrics']['timing_scope'],area_unit=rows[0]['metrics']['area_unit'])


def markdown(record):
    lines=['| Metric | Baseline | Variant | Delta |','| --- | ---: | ---: | ---: |']
    for key,value in record['metrics'].items():
        lines.append('| '+key+' | '+' | '.join('unavailable' if value[k] is None else f'{value[k]:g}' for k in ('baseline','variant','delta'))+' |')
    lines += ['', 'Same hardware/library/tool inputs: '+str(record['same_hardware_inputs'])+'.',record['scope']+'.',record['area_unit']+'.',
              '', 'Configuration changes: '+', '.join(sorted(record['configuration_changes']))+'.',
              'Changed input hashes: '+str(len(record['input_hash_changes']))+'.']
    return '\n'.join(lines)
