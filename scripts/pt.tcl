# scripts/pt.tcl
# Variables injected by pt_shell -x:
#   DESIGN, SDC_FILE, STA_NETLIST, LIB_DBS_RAW, CLK_FREQ_MHZ, TIMING_RPT, RESULT_DIR

foreach v {DESIGN SDC_FILE STA_NETLIST LIB_DBS_RAW CLK_FREQ_MHZ TIMING_RPT RESULT_DIR} {
  if {![info exists $v]} {
    puts stderr "ERROR: variable $v not set. Inject via pt_shell -x \"set $v ...\""
    exit 2
  }
}

file mkdir $RESULT_DIR

# ---- Libraries (.db) ----
set lib_db_list [split $LIB_DBS_RAW " "]
if {[llength $lib_db_list] == 0} {
  puts stderr "ERROR: LIB_DBS_RAW is empty."
  exit 2
}
foreach f $lib_db_list {
  if {![file exists $f]} {
    puts stderr "ERROR: DB not found: $f"
    exit 2
  }
}

# Put db directories into search_path
set sp [list .]
foreach f $lib_db_list { lappend sp [file dirname $f] }
set_app_var search_path $sp

# PrimeTime library setup
# Use the list directly; "*" means search in working design first then libs
set_app_var target_library $lib_db_list
set_app_var link_library   [concat "*" $target_library]

# ---- Enable Power Analysis ----
set power_enable_analysis true
set power_analysis_mode averaged

# ---- Read netlist ----
if {![file exists $STA_NETLIST]} {
  puts stderr "ERROR: STA_NETLIST not found: $STA_NETLIST"
  exit 2
}
read_verilog $STA_NETLIST

# ---- Link ----
current_design $DESIGN
if {[link] == 0} {
  puts stderr "ERROR: Linking failed for design '$DESIGN'. Check undefined modules or library paths."
  exit 2
}

if {[string equal [current_design] ""]} {
  puts stderr "ERROR: current_design is empty after linking. Check top module name '$DESIGN'."
  exit 2
}

puts "Information: Link successful for design $DESIGN"

# ---- Constraints ----
if {![file exists $SDC_FILE]} {
  puts stderr "ERROR: SDC_FILE not found: $SDC_FILE"
  exit 2
}
read_sdc $SDC_FILE

# Optional safeguard: if no clocks defined by SDC, create one on port 'clock'
# (comment out if your SDC always creates clocks)
if {[llength [all_clocks]] == 0} {
  set period_ns [expr 1000.0 / $CLK_FREQ_MHZ]
  if {[sizeof_collection [get_ports clock]] > 0} {
    puts "Warning: No clocks found in SDC. Creating default clock on port 'clock' period=${period_ns}ns"
    create_clock -period $period_ns [get_ports clock]
  } else {
    puts "Warning: No clocks found and no port named 'clock'. Skipping default clock creation."
  }
}

# ---- Reporting ----
# Keep your existing TIMING_RPT as the main output to match Makefile expectations.
# Also dump some useful PT-native reports.
report_design                        > $RESULT_DIR/pt.design.rpt
report_clocks -attributes            > $RESULT_DIR/pt.clock.rpt
report_constraints -all_violators    > $RESULT_DIR/pt.constraints.rpt
report_timing -max_paths 50 -delay_type max -input_pins -nets -transition_time \
                                     > $TIMING_RPT
report_qor                           > $RESULT_DIR/pt.qor.rpt
report_timing -max_paths 50 -delay_type min -input_pins -nets -transition_time \
                                     > $RESULT_DIR/pt.hold.rpt
# Generate Power Report
report_power -hierarchy -levels 3 > $RESULT_DIR/pt.power.rpt

exit
