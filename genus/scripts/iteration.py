"""Design-owned synthesis inputs and checkpoint compatibility contracts."""
import hashlib
import json
from pathlib import Path

STAGES = ('30-elaborate', '40-constraints', '50-generic', '60-map', '70-optimize', '80-reports', '90-export')
LABELS = dict(zip(('elaborate','constraints','generic','map','optimize','reports','export'), STAGES))
HOOKS = {phase+'_'+label:(phase, stage) for label,stage in LABELS.items() for phase in ('before','after')}
CHECKPOINTS = ('50-generic', '60-map', '70-optimize')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def design_inputs(config, base):
    hooks = config.get('hooks', {})
    if not isinstance(hooks, dict) or set(hooks)-HOOKS.keys():
        raise ValueError('Unknown hook; use before_/after_ plus '+', '.join(LABELS))
    result = dict(sdc=None, hooks={}, dependencies=[])
    def resolve(value):
        if not isinstance(value, str) or '\n' in value or '\r' in value:
            raise ValueError('Design input paths must be strings without newlines')
        path = (base/value).resolve()
        if not path.is_file(): raise ValueError('Missing design input: '+str(path))
        return str(path)
    if config.get('sdc'): result['sdc'] = resolve(config['sdc'])
    for point, paths in hooks.items():
        if not isinstance(paths,list): raise ValueError('Hook files must be ordered lists')
        result['hooks'][point] = [resolve(path) for path in paths]
    dependencies = config.get('hook_inputs', [])
    if not isinstance(dependencies,list): raise ValueError('hook_inputs must be a list')
    result['dependencies'] = [resolve(path) for path in dependencies]
    return result


def input_paths(custom):
    return ([custom['sdc']] if custom['sdc'] else []) + [p for paths in custom['hooks'].values() for p in paths] + custom['dependencies']


def load_customization(path):
    return json.loads(Path(path).read_text()) if path else dict(sdc=None,hooks={},dependencies=[])


def environment(custom):
    env = {'FLOW_DESIGN_SDC':custom['sdc'] or ''}
    for point,(phase,stage) in HOOKS.items():
        env['FLOW_HOOK_'+phase.upper()+'_'+stage.upper().replace('-','_')] = '\n'.join(custom['hooks'].get(point, []))
    return env


def checkpoint_settings(config, base, flow):
    value = config.get('checkpoint', {})
    if not isinstance(value,dict) or set(value)-{'save_after','resume_from'}:
        raise ValueError('checkpoint accepts save_after and resume_from')
    if value and flow != 'flat':
        raise ValueError('Native stage checkpoints currently require flat flow; blocks use checked reuse_from')
    saves = value.get('save_after', [])
    if not isinstance(saves,list) or any(stage not in CHECKPOINTS for stage in saves) or len(saves)!=len(set(saves)):
        raise ValueError('save_after must list distinct stages: '+', '.join(CHECKPOINTS))
    resume = str((base/value['resume_from']).resolve()) if value.get('resume_from') else None
    if resume and not Path(resume).is_file(): raise ValueError('Missing checkpoint manifest: '+resume)
    return dict(save_after=saves,resume_from=resume)


def checkpoint_identity(args, stage, custom):
    """Only earlier/equal stages affect compatibility; later hooks/efforts may change."""
    cutoff = int(stage[:2])
    files = [args.rtl,args.blackboxes,args.std_lib,args.spec] + sorted(args.sram_lib_dir.glob('*.lib'))
    # Native DBs embed absolute library/source paths: require the same paths.
    inputs = {str(Path(path).resolve()):digest(path) for path in files}
    canonical = lambda path: custom.get('origins', {}).get(path, path)
    if custom['sdc']: inputs[canonical(custom['sdc'])] = digest(custom['sdc'])
    # Conservative for hook dependencies, which may be read at any hook point.
    inputs.update({canonical(path):digest(path) for path in custom['dependencies']})
    hooks = {}
    for point,paths in custom['hooks'].items():
        if int(HOOKS[point][1][:2]) <= cutoff:
            hooks[point] = [[canonical(path),digest(path)] for path in paths]
    tcl = Path(__file__).resolve().parents[1]/'tcl'
    sources = {str(p.relative_to(tcl)):digest(p) for p in (tcl/'stages').glob('*.tcl') if int(p.name[:2])<=cutoff}
    for name in ('run-synthesis-stages.tcl','synthesize-flat.tcl','iteration-control.tcl'):
        sources[name] = digest(tcl/name)
    sources['iteration.py'] = digest(__file__)
    sources['run_genus.py'] = digest(Path(__file__).with_name('run_genus.py'))
    keys = ('top','hierarchy','defines','clk_port','period_ps','clk_transition_ps','uncertainty_ps',
            'hold_uncertainty_ps','mode','io_fraction','io_min_ps','input_transition_ps','output_load_ff')
    settings = {key:getattr(args,key) for key in keys}
    for name,number in (('generic_effort',50),('map_effort',60),('opt_effort',70)):
        if number<=cutoff: settings[name] = getattr(args,name) or args.effort
    return dict(schema_version=1,stage=stage,inputs=inputs,hooks=hooks,sources=sources,settings=settings,
                genus_executable_sha256=digest(args.genus))


def validate_checkpoint(path, args, custom):
    path = Path(path)
    record = json.loads(path.read_text())
    stage = record.get('stage')
    if stage not in CHECKPOINTS or record.get('schema_version')!=1:
        raise ValueError('Unsupported checkpoint manifest')
    if record['identity'] != checkpoint_identity(args,stage,custom):
        raise ValueError('Checkpoint incompatible: earlier inputs, constraints, hooks, settings, sources or tool changed')
    for name,expected in record['artifacts'].items():
        artifact = path.parent/name
        if Path(name).name!=name or not artifact.is_file() or digest(artifact)!=expected:
            raise ValueError('Checkpoint artifact changed: '+name)
    if set(record['artifacts'])!={'design.db','constraints.sdc','tool-version.txt'}:
        raise ValueError('Incomplete checkpoint artifacts')
    return record


def seal_checkpoints(out, args, custom, log, identities):
    """Seal only stages with a native completion marker, including before later failure."""
    for stage in args.save_checkpoint:
        folder = out/'checkpoints'/stage
        if 'CORAL_GENUS_CHECKPOINT_OK '+stage not in log: continue
        artifacts = {name:digest(folder/name) for name in ('design.db','constraints.sdc','tool-version.txt')
                     if (folder/name).is_file() and (folder/name).stat().st_size}
        if len(artifacts)!=3: raise ValueError('Native checkpoint output missing: '+stage)
        if identities[stage] != checkpoint_identity(args,stage,custom):
            raise ValueError('Checkpoint inputs changed during native execution: '+stage)
        record = dict(schema_version=1,stage=stage,identity=identities[stage],artifacts=artifacts,
                      scope='Native Genus database; compatibility checks do not establish equivalence or timing closure')
        (folder/'checkpoint.json').write_text(json.dumps(record,indent=2)+'\n')
