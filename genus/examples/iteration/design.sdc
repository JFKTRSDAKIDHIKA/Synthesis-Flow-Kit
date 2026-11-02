# Example only: ps/fF, 400 MHz, assumed 20% external I/O budgets.
# Replace the port and interface budgets with your design's timing contract.
create_clock -name core_clock -period 2500 [get_ports clock]
set_clock_uncertainty -setup 100 [get_clocks core_clock]
set_clock_uncertainty -hold 50 [get_clocks core_clock]
set_clock_transition 20 [get_clocks core_clock]
set data_inputs [remove_from_collection [all_inputs] [get_ports clock]]
set_input_delay -max 500 -clock core_clock $data_inputs
set_input_delay -min 100 -clock core_clock $data_inputs
set_output_delay -max 500 -clock core_clock [all_outputs]
set_output_delay -min 100 -clock core_clock [all_outputs]
set_input_transition 50 $data_inputs
set_load 4 [all_outputs]
