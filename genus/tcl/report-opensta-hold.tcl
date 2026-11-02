# Separate min-delay STA of the final mapped netlist. No physical closure claim.
if {[catch {
    set ::sta_continue_on_error 0
    foreach name {TOP OUT RTL SDC STD_LIB SRAM_LIB_DIR PERIOD_PS} {
        if {![info exists ::env(FLOW_$name)]} {error "Missing FLOW_$name"}
    }
    set out $::env(FLOW_OUT)
    read_liberty $::env(FLOW_STD_LIB)
    foreach lib [lsort [glob -nocomplain [file join $::env(FLOW_SRAM_LIB_DIR) *.lib]]] {read_liberty $lib}
    read_verilog $::env(FLOW_RTL)
    if {![link_design -no_black_boxes $::env(FLOW_TOP)]} {error "Hold STA link failed"}
    # Genus-exported SDC declares its time/capacitance units. Read it unchanged.
    read_sdc $::env(FLOW_SDC)
    set_cmd_units -time ps -capacitance fF
    set clocks [get_clocks core_clock]
    if {[llength $clocks]!=1 || abs([get_property $clocks period]-$::env(FLOW_PERIOD_PS))>0.01} {
        error "Hold STA clock period mismatch"
    }
    report_units
    set unit_stream [open [file join $out units.rpt] w]
    puts $unit_stream "Report time: ps; capacitance: fF; verified core_clock period: [get_property $clocks period] ps"
    close $unit_stream
    check_setup -verbose > [file join $out timing-coverage.rpt]
    report_checks -path_delay min -format full_clock_expanded -group_path_count 100 -endpoint_path_count 1 -digits 3 > [file join $out hold.rpt]
    report_worst_slack -min -digits 3 > [file join $out worst-hold-slack.rpt]
    report_checks -path_delay max -format full_clock_expanded -group_path_count 100 -endpoint_path_count 1 -digits 3 > [file join $out setup-crosscheck.rpt]
    puts "CORAL_OPENSTA_HOLD_OK"
} message]} {
    puts stderr "HOLD_STA_FAILED: $message"
    exit 2
}
exit 0
