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
   Report native structure, timing intent, clocks, area, setup and hold; assert
   the clock period and macro counts again. Save these in `native-final/`.
7. Recheck input and methodology hashes and publish `entry-status.json`.

Defaults are one native job and two Genus threads. The sampled memory guard
pauses only its own process group below 8 GiB host availability, resumes above
12 GiB, and stops that group above 32 GiB proportional memory (PSS). This is
sampled protection, not OS isolation. Exit 75 means admission/lock/resource
retry; use a new attempt directory after execution has produced partial results.
An external queue may schedule retries by calling this same entry. It must
preserve each attempt. Ordinary tool/qualification failures exit 2.

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

`run.py` remains the public entrance. Its snapshot runs `tcl/genus.tcl` (flat)
or `tcl/genus-block.tcl` (block), then `tcl/stage-driver.tcl` sources these files
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

These files are session stages, not standalone shell entry points. Splitting a
file does not provide safe intermediate restart: the launcher still executes a
complete fresh session and preserves partial attempts. Native database save/
restore and state qualification would be required before exposing stage resume.
No retiming, pipeline insertion or additional timing exceptions are enabled by
this organization.

The assembled hierarchy is checked in a separate fresh session. Its driver,
`tcl/final-check.tcl`, sources `tcl/final/10-restore.tcl`, `20-validate.tcl`,
`30-reports.tcl` and `40-export.tcl`. They restore the netlist/original SDC,
assert the clock and macro inventory, report native structure/timing/area,
and export the restored SDC. They perform no synthesis optimization.

The generic I/O convention reserves `period_ps * io_fraction` externally;
with 2500 ps and fraction 0.2 this is 500 ps. It is an assumed interface budget,
not a measured external delay. Select interface assumptions explicitly when
qualifying a configuration. The `reg2reg` mode instead excludes I/O paths and
must not be reported as complete interface closure.

The historical `mlem-n6-synth.tcl` template remains a frozen replay artifact;
it is not refactored into the new AMEOOO/RVV/VME methodology. `power.tcl` is an
auxiliary power-analysis script, not an automatically invoked synthesis stage.

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
