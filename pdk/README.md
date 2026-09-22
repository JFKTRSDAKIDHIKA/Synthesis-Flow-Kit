# PDKs

| directory | contents | how it is obtained |
| --- | --- | --- |
| `nangate45/` | NangateOpenCell 45 nm: Liberty (fast/typ/slow), LEF, GDS, and the FakeRAM-derived `fakeram45_*` macros | checked in |
| `USC-3N-2D/` | USC-3N-2D 3 nm GAA: `3nm_GAA_FSPR*` (front-side power rail) and `3nm_GAA_BPR*` (buried power rail) Liberty/LEF, plus the `openpiton_mem_L3_256k` compiled SRAM instances | git submodule, <https://github.com/SCCAD-LAB/USC-3N-2D> |

Both are read-only inputs. Nothing in a flow may write into them; a flow that
needs a corrected Liberty (for example the USC-3N-2D standard-cell Liberty,
which ships with `area : 0;` on every cell) writes the corrected copy into its
own build directory.

`pdk/asap7sc7p5t_28` used to be a gitlink with no `.gitmodules` entry, so it
could never be cloned and the directory was always empty. It was removed. To
use `make PDK=asap7sc7p5t_28`, clone the ASAP7 release into `pdk/asap7sc7p5t_28`
by hand.
