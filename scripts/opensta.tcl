# ==============================================================================
# OpenSTA script (OpenSTA 2.7.0 compatible)
# Required env:
#   DESIGN, LIB_FILE, STA_NETLIST, SDC_FILE, TIMING_RPT
# Optional:
#   CLK_FREQ_MHZ
# ==============================================================================

proc getenv_or_fail {k} {
  if {![info exists ::env($k)] || $::env($k) eq ""} {
    puts stderr "FATAL: env($k) is not set."
    exit 2
  }
  return $::env($k)
}

set top [getenv_or_fail "DESIGN"]
set lib [getenv_or_fail "LIB_FILE"]
set net [getenv_or_fail "STA_NETLIST"]
set sdc [getenv_or_fail "SDC_FILE"]
set rpt [getenv_or_fail "TIMING_RPT"]

set clk_mhz ""
if {[info exists ::env(CLK_FREQ_MHZ)]} { set clk_mhz $::env(CLK_FREQ_MHZ) }

# ---- Read inputs ----
read_liberty $lib
read_verilog $net
link_design $top
read_sdc $sdc

# ---- Open report ----
set fp [open $rpt "w"]
puts $fp "===== OpenSTA Report ====="
puts $fp "top     = $top"
puts $fp "lib     = $lib"
puts $fp "netlist = $net"
puts $fp "sdc     = $sdc"
if {$clk_mhz ne ""} { puts $fp "CLK_FREQ_MHZ = $clk_mhz" }
puts $fp ""

# ---- Units ----
puts $fp "----- Units -----"
puts $fp [report_units]
puts $fp ""

# ---- Clocks ----
puts $fp "----- Clocks (names) -----"
set clks [all_clocks]
puts $fp $clks
puts $fp ""

puts $fp "----- Clock properties -----"
foreach c $clks {
  puts $fp [report_clock_properties $c]
}
puts $fp ""

if {[llength $clks] == 0} {
  puts $fp "WARN: No clocks found after read_sdc."
  puts $fp "      If SDC creates clocks conditionally, confirm env(CLK_FREQ_MHZ) and clock port name."
  puts $fp ""
}

# ---- Summary ----
puts $fp "----- Summary -----"
puts $fp [report_worst_slack -max]
puts $fp [report_worst_slack -min]
puts $fp [report_tns -max]
puts $fp [report_tns -min]
puts $fp [report_wns -max]
puts $fp [report_wns -min]
puts $fp ""

# ---- Main checks ----
puts $fp "----- report_checks (setup/max) -----"
puts $fp [report_checks -path_delay max -digits 3]
puts $fp ""

puts $fp "----- report_checks (hold/min) -----"
puts $fp [report_checks -path_delay min -digits 3]
puts $fp ""

# ---- Debug: unconstrained ----
puts $fp "----- debug: report_checks -unconstrained (max) -----"
puts $fp [report_checks -unconstrained -path_delay max -digits 3]
puts $fp ""

puts $fp "----- debug: report_checks -unconstrained (min) -----"
puts $fp [report_checks -unconstrained -path_delay min -digits 3]
puts $fp ""

close $fp
exit
