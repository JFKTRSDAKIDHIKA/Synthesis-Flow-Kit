# Fresh native restore of the assembled mapped hierarchy; no optimization.
foreach name {TOP OUT RTL SDC STD_LIB SRAM_LIB_DIR THREADS PERIOD_PS EXPECTED_MACROS} {
    if {![info exists ::env(FLOW_$name)]} {error "Missing FLOW_$name"}
}
set top $::env(FLOW_TOP)
set out $::env(FLOW_OUT)
set_db hdl_enable_real_support true
set_db auto_ungroup none
set_db max_cpus $::env(FLOW_THREADS)
set_db lp_insert_clock_gating false
set_db library [concat [list $::env(FLOW_STD_LIB)] [glob -nocomplain [file join $::env(FLOW_SRAM_LIB_DIR) *.lib]]]
read_netlist -top $top $::env(FLOW_RTL)
read_sdc $::env(FLOW_SDC)
set_time_unit -picoseconds
set_load_unit -femtofarads
