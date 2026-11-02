# Shared native timing constraints. All timing/load values use ps and fF.
set_time_unit -picoseconds
set_load_unit -femtofarads
if {$flow_kind eq "block" && [need FLOW_CLK_PORT] eq "__virtual_clock__"} {
  set clock [get_ports -quiet __no_physical_clock__]
  create_clock -name core_clock -period [need FLOW_PERIOD_PS]
} else {
  set clock [get_ports [need FLOW_CLK_PORT]]
  if {[llength $clock] != 1} { error "Clock port is missing or ambiguous" }
  create_clock -name core_clock -period [need FLOW_PERIOD_PS] $clock
}
set measured_period [get_db [get_db clocks core_clock] .period]
if {abs($measured_period - [need FLOW_PERIOD_PS]) > 0.01} { error "Native clock period mismatch: $measured_period" }
puts "CORAL_NATIVE_CLOCK_PERIOD_PS=$measured_period"
set_clock_transition [need FLOW_CLK_TRANSITION_PS] [get_clocks core_clock]
set_clock_uncertainty -setup [need FLOW_UNCERTAINTY_PS] [get_clocks core_clock]
set_clock_uncertainty -hold [need FLOW_HOLD_UNCERTAINTY_PS] [get_clocks core_clock]
set data_inputs [remove_from_collection [all_inputs] $clock]
if {[need FLOW_STA_MODE] eq "reg2reg"} {
  set_false_path -from $data_inputs
  set_false_path -to [all_outputs]
} else {
  set delay [expr {[need FLOW_PERIOD_PS] * [need FLOW_IO_FRACTION]}]
  set_input_delay -max $delay -clock core_clock $data_inputs
  set_input_delay -min [need FLOW_IO_MIN_PS] -clock core_clock $data_inputs
  set_output_delay -max $delay -clock core_clock [all_outputs]
  set_output_delay -min [need FLOW_IO_MIN_PS] -clock core_clock [all_outputs]
}
set_input_transition [need FLOW_INPUT_TRANSITION_PS] $data_inputs
set_load [need FLOW_OUTPUT_LOAD_FF] [all_outputs]
report_clocks > [file join $out reports clocks.rpt]
write_sdc > [file join $out reports constraints.sdc]
