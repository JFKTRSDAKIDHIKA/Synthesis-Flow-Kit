# Genus reference methodology

`run.py` is the only supported entry point for new Genus synthesis. Everything
needed to prepare inputs, elaborate blocks, run native mapping, assemble the
hierarchy and restore it for native checks is owned by this folder.

## Run

From the workspace root:

```sh
source .workspace/env.sh
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py check /absolute/path/to/design.json
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py synth /absolute/path/to/design.json
```

Copy and edit `examples/ameooo.json`, `examples/rvv.json` or `examples/vme.json`.
They are configuration templates, not qualified new hardware identities. Select
an explicit platform ID, top, prepared RTL, blackboxes, standard-cell Liberty,
SRAM Liberty directory, macro spec and unique output directory. Carry the
hardware/source qualification and exact library corner from the selected
configuration; never infer them from a directory name. Use `input_sha256` to
assert previously qualified input hashes, keyed by absolute or config-relative
filenames. Paths resolve relative to the JSON file, regardless of shell cwd.
No private PDK, RTL, license setting or technology file is bundled here.

Store synthesis attempts under `Synthesis-Flow-Kit/runs/<unique-campaign-id>/`
(Git-ignored), outside the `genus/` source folder. They are synthesis results,
not PnR results. Configurations still name an explicit fresh `out_dir`.
The completed methodology/iteration validation campaigns now live there;
compatibility symlinks at their original PnR paths preserve frozen absolute
references in configs, checkpoints and logs. Their contents were hash-verified
after relocation without editing frozen records.


`check` validates paths, settings, tools and any asserted hashes, then prints
the launch intent. It creates no output and launches no EDA tool. It does not
qualify generated RTL, prove equivalence or reserve a license.

`synth` runs these stages:

1. Validate the explicit inputs and settings; acquire the shared `lock_file`.
2. Require the configured RAM admission headroom (36 GiB by default).
3. Freeze `launch.json`, `configuration.json` and a complete `flow-source/`
   snapshot of the executable methodology. Record input, tool and source hashes.
4. Elaborate and lower RTL with Slang/Yosys, preserving the selected block
   boundaries. Genus performs technology mapping; Yosys does not replace it.
5. Synthesize selected blocks bottom-up, validate each mapped result, and
   assemble the complete hierarchy with exact macro and clock/structure checks.
6. Restore the assembled netlist and top-level SDC in a fresh Genus session.
   Report native structure, timing intent, clocks, area and setup; assert
   the clock period and macro counts again. Save these in `native-final/`.
7. Run independent OpenSTA min-delay analysis of the final mapped netlist and
   exported SDC. Preserve detailed hold paths, library hold requirements, slack,
   coverage and engine/corner provenance in `native-hold/`. Copy the labelled
   hold report to `native-final/hold.rpt` for discovery and comparison.
8. Recheck input and methodology hashes and publish `entry-status.json`.

Defaults are one native job and two Genus threads. The sampled memory guard
pauses only its own process group below 8 GiB host availability, resumes above
12 GiB, and stops that group above 32 GiB proportional memory (PSS). This is
sampled protection, not OS isolation. Exit 75 means admission/lock/resource
retry; use a new attempt directory after execution has produced partial results.
An external queue may schedule retries by calling this same entry. It must
preserve each attempt. Ordinary tool/qualification failures exit 2.

Genus 23.14 `report_timing` does not support `-early`. A required separate
OpenSTA stage computes actual min-delay/hold checks using `report_checks
-path_delay min`, including the library hold requirement and data arrival/
required times. `tools.sta` selects the OpenSTA executable (default `sta`). A
missing tool, failed link, SDC error or missing completion marker fails the run.
Hold violations remain report results; execution success does not mean closure.

By default, hold uses the same Liberty files/corner as synthesis. An explicit
`hold_sta` configuration can select alternate min-corner libraries:

```json
{
  "tools": {"genus": "genus", "yosys": "yosys", "sta": "sta"},
  "hold_sta": {
    "corner_id": "REPLACE_WITH_QUALIFIED_FAST_CORNER",
    "std_lib": "/absolute/path/to/fast-standard.lib",
    "sram_lib_dir": "/absolute/path/to/fast-macro-libs"
  }
}
```

Alternate libraries require all three fields; they must match the mapped cell
and macro interfaces. All Liberty files and the STA executable are recorded
and hashed. The stage reads the Genus-exported SDC unchanged (including its
unit declarations), then reports in ps/fF and verifies the clock period. It
refuses unresolved black boxes and records timing coverage. It also writes a
setup cross-check using the same engine/model. Empty coverage output means
`check_setup` reported no issues; it is not a proof of complete constraints.

This is **pre-layout hold STA** with ideal clocks, pin/load capacitances and
no extracted interconnect. A same-corner result is not worst-case PVT coverage.
Post-CTS/routed hold closure requires the corresponding clocks, parasitics,
min-corner libraries and a qualified downstream STA/signoff flow. The report
provenance states these limits; no placeholder or setup slack substitutes for
hold analysis.

Success means mapping/structural execution completed and native reports were
produced. It **does not mean setup/hold closure, full equivalence, routed physical
closure or signoff**. Review `native-final/{setup,hold,timing-intent}.rpt` and the
selected configuration's qualification requirements before publishing PPA.

Do not reuse an output directory. `plan CONFIG` optionally runs elaboration and
structural planning without licensed Genus mapping; it also needs a fresh
output directory. Use a separate plan configuration/output if planning before
synthesis. Flat synthesis (`"flow": "flat"`) needs `options.clk_port` and supports
`check` and `synth`; its other inputs are the same, and `options.blocks` is omitted.
An explicit `options.period_ps` is always required (400 MHz is 2500 ps).

The configuration reference is the strict option parser in `run.py`. Unknown
keys and flow-incompatible options are errors. The `options` values use the
backend names with underscores: `blocks`, `defines`, `clean_blocks` and
`flatten_blocks` accept lists; Boolean factoring/repair controls accept booleans.
Timing units are ps and fF. The flat flow defaults to I/O constraints, not
false paths on every input/output.

## Tcl stage methodology

Both AMEOOO and RVV/VME use the same synthesis stages. Their JSON profiles
supply the top, source files, library corner, macro inventory, hierarchy and
interface assumptions. A different processor does not require a copy of the
mapping algorithm. Flat and bottom-up block drivers select their explicit
elaboration differences; the block driver reads and preserves child netlists
and permits a virtual clock for combinational units.

`run.py` remains the public entrance. Its snapshot runs `tcl/synthesize-flat.tcl` (flat)
or `tcl/synthesize-hierarchical-block.tcl` (block), then `tcl/run-synthesis-stages.tcl` sources these files
in order in one Genus session:

| Stage file under `tcl/stages/` | Responsibility | Main output/state |
| --- | --- | --- |
| `00-setup.tcl` | Required settings, hierarchy policy, effort and CPU limits | Configured session |
| `10-libraries.tcl` | Standard-cell and SRAM Liberty views | Loaded target libraries |
| `20-read-hdl.tcl` | Defines, RTL/blackboxes and optional child netlists | Read HDL |
| `30-elaborate.tcl` | Elaborate, preserve children, apply hierarchy policy, check unresolved references | `reports/elaboration.rpt` |
| `40-constraints.tcl` | Explicit ps/fF units, clock, uncertainty, I/O budgets, transitions and loads | `reports/{clocks.rpt,constraints.sdc}` |
| `50-generic.tcl` | `syn_generic` | Generic logic |
| `60-map.tcl` | `syn_map` | Technology-mapped logic |
| `70-optimize.tcl` | `syn_opt` | Optimized mapped logic |
| `80-reports.tcl` | Area, timing and power reports | `reports/genus-*.rpt` |
| `90-export.tcl` | Export the raw mapped netlist for downstream normalization/checks | `netlist/genus-raw.v` |

A failure propagates out of the driver before its completion marker. Log markers
`CORAL_GENUS_STAGE_BEGIN/END` identify the last entered/completed stage. All stage
files are frozen with the campaign source snapshot and included in block cache
identity; editing a stage invalidates its old mapping identity.

These files are session stages, not standalone shell entry points. Use the
checkpoint interface below for supported stage restart. No retiming, pipeline
insertion or additional timing exceptions are enabled by this organization.

The assembled hierarchy is checked in a separate fresh session. Its driver,
`tcl/verify-mapped-netlist.tcl`, sources `tcl/final/10-restore.tcl`, `20-validate.tcl`,
`30-reports.tcl` and `40-export.tcl`. They restore the netlist/original SDC,
assert the clock and macro inventory, report native structure/timing/area,
and export the restored SDC. They perform no synthesis optimization.

The generic I/O convention reserves `period_ps * io_fraction` externally;
with 2500 ps and fraction 0.2 this is 500 ps. It is an assumed interface budget,
not a measured external delay. Select interface assumptions explicitly when
qualifying a configuration. The `reg2reg` mode instead excludes I/O paths and
must not be reported as complete interface closure.

The historical `legacy-mlem-n6-synthesis.tcl` template remains a frozen replay artifact;
it is not refactored into the new AMEOOO/RVV/VME methodology. `report-opensta-leakage-power.tcl` is an
auxiliary power-analysis script, not an automatically invoked synthesis stage.
The legacy replay adapter still reads `scripts/mlem-n6-synth.tcl` from the
selected frozen campaign; renaming the template here does not change that
historical input contract.

## Repeated design iterations

Routine iterations change a JSON profile, a design-owned SDC or a design-owned
hook, rather than editing the Python launcher or shared stage Tcl. Use a fresh
output directory for every variant. `examples/iteration/` demonstrates the
configuration fields without selecting a qualified hardware platform.

### Design SDC and Tcl hooks

Optional top-level configuration fields:

```json
{
  "sdc": "constraints/design.sdc",
  "hooks": {
    "before_optimize": ["hooks/targeted-optimization.tcl"],
    "after_optimize": ["hooks/check-optimization.tcl"]
  },
  "hook_inputs": ["hooks/shared-settings.tcl"]
}
```

The SDC **replaces** the generic clock/I/O budget. It must define `core_clock`
with the configured `options.period_ps`. It owns clock uncertainty, I/O budgets,
transitions, loads and any justified timing exceptions. The flow sets ps/fF
before reading it and verifies the clock. The final exported SDC captures hook
changes made before export and is used in the independent final restore.

Hook keys are `before_` or `after_` plus `elaborate`, `constraints`, `generic`,
`map`, `optimize`, `reports` or `export`. Each value is an ordered list of Tcl
files; errors stop the flow. Hooks execute in the current Genus session at
global Tcl scope. Keep changes appropriate to the stage: hooks after reports
or export are for observation, not changes to the design or timing intent.
Use `[file dirname [info script]]` for sibling helper paths. List every extra
file a hook/SDC sources or reads in `hook_inputs`; arbitrary Tcl dependency
tracing is not automatic. Files, including dependencies, are copied with their
relative directory relationships preserved under `design-inputs/files/` and
hashed before/after execution. Undeclared external dependencies are outside
this reproducibility contract. Do not mutate the flow's `FLOW_*` environment,
driver variables or output paths from a hook.

In flat flow these settings apply to the complete design. In bottom-up flow
they apply **only to the top unit**, whose port names match the design SDC;
child blocks retain their generic interface constraints. They do not derive
child timing budgets from top-level STA. All customization input hashes enter
the block cache identity conservatively, so changes may remap child units too.

`options.generic_effort`, `options.map_effort` and `options.opt_effort` select
individual effort levels; each defaults to `options.effort`. Change those
configuration fields rather than editing the stage script to sweep effort.

### Native checkpoints and restart

Flat flow can save native Genus databases after these stages:

```json
{
  "checkpoint": {"save_after": ["50-generic", "60-map", "70-optimize"]}
}
```

Each selected `checkpoints/STAGE/` contains `design.db`, `constraints.sdc`,
`tool-version.txt` and a hash-sealed `checkpoint.json`. Only a native completed
checkpoint is sealed, including one saved before a later stage fails. This
uses Genus Common UI `write_db -all_root_attributes` / `read_db`; these retain
native design/setup state rather than approximating restart from Verilog.

To iterate from mapping, create a new configuration/output directory:

```json
{
  "out_dir": "/absolute/path/to/new-attempt",
  "checkpoint": {"resume_from": "/absolute/path/to/baseline/checkpoints/60-map/checkpoint.json"},
  "options": {"period_ps": 2500, "clk_port": "clock", "opt_effort": "high"}
}
```

The example shows the relevant fields; retain all other required design inputs.
`check` validates checkpoint hashes and compatibility without taking a license.
`synth` restores it, verifies the native tool version and clock, and executes
only later stages, followed by the usual mapped-netlist checks and cold final
restore. Earlier/equal stage inputs, scripts, hooks, efforts and tool identity
must match. Later hooks and efforts may change; e.g. a mapping checkpoint permits
changing `opt_effort`, but not `map_effort`, the RTL, SDC or library. Change the
latter inputs by starting fresh. CPU count is reapplied for the new attempt.
Preserve the origin attempt and the original library/source paths: native
checkpoints can refer to those paths. This is compatibility validation, not a
proof of equivalence or identical optimization results to a fresh full run.

Native stage restart currently supports flat flow only. Bottom-up runs use the
existing checked `options.reuse_from` mapped-block cache. Do not infer that a
top-level checkpoint can replace child-block qualification.

### Comparing attempts

```sh
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py compare /path/to/baseline /path/to/variant
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py compare /path/to/baseline /path/to/variant --json --out /path/to/comparison.json
```

Every successful synthesis writes `metrics.json`. Comparison reads retained
native final reports and prints baseline/variant/delta for worst reported setup
and hold slack (ps), cell area and cell count. Unsupported/missing report values
remain unavailable; they are never zero-filled. Cell area is in the Liberty
area convention, which must be verified before calling it square micrometres.
Timing covers retained report paths, not a new routed signoff claim.

The JSON also records configuration and input-hash differences. Different
hardware/platform/library/tool identities are rejected by default; use
`--allow-different-inputs` for an explicitly labelled comparison. Period, SDC
and hook changes are visible experiment differences; a better slack under a
looser constraint must not be interpreted as a faster implementation.
Comparison is read-only and refuses to overwrite an existing `--out` file.

## Input preparation

```sh
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py prepare models --help
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py prepare bind --help
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py prepare bind-wide --help
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py prepare verify-wide --help
```

The AMEOOO wide-MRF binding preserves both read replicas and every address,
control and data lane. The public preparation helpers and their imports and
testbench are all inside this folder. Generated SRAM models remain explicitly
estimated interfaces; relocation does not qualify them as native N6 SRAM IP.
Physical LEF adaptation and placement/route implementation remain owned by the
PnR kit; they are not synthesis stages.

The RVV/VME boundary repair is limited to the three certified leaked genvar
parameter overrides. A previously repaired generic snapshot requires an explicit
`options.genvar_repair_proof` file. The helper does not discover a campaign or
read a neighboring `contract.json`. A structural inverse and source hash are
checked before acceptance.

## Cache and reproducibility

`options.reuse_from` is an explicit preserved result directory. Its complete
origin source is required: `flow-source/scripts/genus_blocks.py` for this
methodology, or the historical campaign's `synthesis-flow/scripts/` layout.
Origin driver hashes, exact newly prepared unit RTL, native mapping settings,
child netlist hashes, original cache keys and mapped netlist hashes must match.
A mismatch causes remapping or a failed qualification, never an unchecked copy.
Some older native driver prefixes are intentionally incompatible. Merely moving
files does not authorize weakening that check. Manually sealed/offline prepared
campaigns are not adopted by this fresh-output launcher.

`launch.json` and `flow-source/` make a result independent of later source edits.
The methodology refuses an input or source change during execution. Preserve
these records with the result; do not commit raw logs, netlists, databases or
PDK views to the methodology repository.

## Layout

| Path | Responsibility |
| --- | --- |
| `run.py` | Public configuration, locks, snapshot, execution and final restore |
| `examples/` | Editable AMEOOO/RVV/VME configuration templates |
| `scripts/genus_blocks.py` | Elaboration, proof-backed factoring, block dependencies and assembly |
| `scripts/run_genus.py` | Internal native Genus driver and mapped-netlist validation |
| `scripts/{genus_*,netlist_lib,assemble_netlist,hierarchical_area,...}.py` | Mapping, proof, parser and report support |
| `scripts/prepare_*.py`, `scripts/repair_genvars.py` | Input/binding/model preparation and checked metadata repair |
| `scripts/resource_runtime.py` | Own-process-group sampled resource guard |
| `scripts/iteration.py`, `scripts/compare_runs.py` | Design inputs, checkpoint compatibility and comparison |
| `scripts/hold_sta.py`, `tcl/report-opensta-hold.tcl` | Required independent OpenSTA min-delay/hold analysis |
| `tcl/`, `tcl/stages/`, `tcl/final/` | Small ordered drivers, shared synthesis stages and separate cold verification stages |
| `rtl/` | Proof-backed reusable factoring helper RTL |
| `tests/` and `scripts/test_*.py` | Launcher integration, structural and normalization regression tests |
| `import-provenance.json` | Original source paths/hashes for this consolidation; no runtime dependency |

The source imports are from the frozen RVV automatic-continuation flow and the
PnR kit's memory-preparation utilities. Campaign-specific AMEOOO control-repair
attempts and their proofs remain frozen evidence; they have not been silently
promoted to generic optimizers. New optimization code must enter this folder
with its own proof and regression coverage.

## Migration and frozen jobs

Already active/queued AMEOOO, RVV and VME campaigns continue through their
original frozen controllers. Their automatic retries belong to the same frozen
attempt series. Do not rewrite them, move their dependencies, change their
source identities or restart them as part of this source reorganization.
Every newly prepared Genus campaign uses this entry and its source snapshot.
Higher-level launchers call it; they do not fork the implementation.

The PnR MLEM controller now runs Innovus stages only. For an explicitly frozen
historical MLEM synthesis contract, use:

```sh
python3 coralnpu/Synthesis-Flow-Kit/genus/run.py legacy-mlem /absolute/path/to/manifest.json
```

This requires its frozen synthesis Tcl in `input-lock.json`, its explicit shared
`genus_lock`, and unchanged inputs. It preserves the historical clock/library
identity and writes the synthesis completion marker consumed by the PnR
controller. It does not select MLEM as a new supported benchmark platform.
Old PnR memory scripts and the old synthesis Tcl remain for recorded hashes and
historical replay; they are not the editable source or entrance for new synthesis.

## Test

```sh
source .workspace/env.sh
make -C coralnpu/Synthesis-Flow-Kit genus-test
```

The entry tests use fake Genus tools and never consume a commercial license.
The Tcl stage tests execute the real snapshotted drivers with command stubs in
`tclsh`, checking stage order, child reads, failure propagation and the absence
of optimization during final restore. These stubs do not qualify native commands.
The normalization tests use Yosys when available. Passing these tests validates
the source migration and launcher plumbing, not a new full-chip native result.
