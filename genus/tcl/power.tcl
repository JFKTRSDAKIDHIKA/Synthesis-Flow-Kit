# SPDX-License-Identifier: Apache-2.0
# OpenSTA static (leakage) power of a synthesized CoralNPU netlist.
#
# The same environment contract as flow/tcl/sta.tcl, reduced to what
# report_power needs. Leakage power is activity-independent, so no switching
# activity is annotated; the internal/switching columns of the report are not
# meaningful and only the leakage column is consumed downstream.
#
#   NETLIST       mapped gate-level netlist (fanout-repaired)
#   TOP           top module
#   STD_LIB       standard-cell Liberty
#   SRAM_LIB_DIR  directory of macro Liberty files; all of them are read
#   CLK_PORT      the one clock port
#   PERIOD        clock period, in the Liberty's time unit
#   RPT           report file

proc need {k} {
  if {![info exists ::env($k)] || $::env($k) eq ""} {
    puts stderr "FATAL: env($k) unset"
    exit 2
  }
  return $::env($k)
}

set netlist  [need NETLIST]
set top      [need TOP]
set std_lib  [need STD_LIB]
set sram_dir [need SRAM_LIB_DIR]
set clk_port [need CLK_PORT]
set period   [need PERIOD]
set rpt      [need RPT]

read_liberty $std_lib
foreach f [lsort [glob -nocomplain [file join $sram_dir *.lib]]] {
  read_liberty $f
}
read_verilog $netlist
link_design $top

if {[llength [get_ports $clk_port]] == 0} {
  puts stderr "FATAL: no port $clk_port on $top"
  exit 2
}
create_clock -name core_clock -period $period [get_ports $clk_port]

report_power > $rpt
report_power
exit
