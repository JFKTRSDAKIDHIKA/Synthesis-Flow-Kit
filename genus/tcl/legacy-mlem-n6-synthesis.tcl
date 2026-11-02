# Explicit library inputs and a frozen core-block contract; native Genus mapping.
set root $::env(MLEM_N6_CAMPAIGN)
source $root/scripts/config.tcl
set_db max_cpus_per_server 8
set_db library [list $cfg(std_lib) $cfg(macro_lib)]
read_hdl -v2001 [list $root/inputs/retarget.v $root/inputs/memory-adapter.v]
elaborate mlem_core
set_time_unit -nanoseconds
set_load_unit -picofarads
read_sdc $root/inputs/frozen.sdc
# Genus clock DB attributes remain in ps even when SDC input units are ns.
foreach {clock expected} {core_clk 12500 jtag_clk 20000 ref_clk 50000} {
    set actual [get_db [get_db clocks $clock] .period]
    if {abs($actual - $expected) > 0.000001} {
        error "Clock unit mismatch: $clock period $actual, expected $expected ps"
    }
    puts "MLEM_CLOCK_PERIOD_PS $clock $actual"
}
check_design -unresolved > $root/reports/synth-unresolved.rpt
set_db syn_generic_effort medium
set_db syn_map_effort high
set_db syn_opt_effort high
syn_generic
syn_map
syn_opt
write_hdl > $root/synthesis/mlem_core.v
write_sdc > $root/synthesis/mlem_core.sdc
write_db $root/synthesis/mlem_core.db
report_area > $root/reports/synth-area.rpt
report_clocks > $root/reports/synth-clocks.rpt
report_timing -max_paths 30 > $root/reports/synth-timing.rpt
report_qor > $root/reports/synth-qor.rpt
puts MLEM_N6_SYNTH_COMPLETE
exit
