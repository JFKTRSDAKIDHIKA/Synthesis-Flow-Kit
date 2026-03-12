# scripts/dc.tcl

# ==================================================
# Parameter overrides from shell environment
# Example:
#   ARRAY_M=16 ARRAY_N=32 dc_shell -f scripts/dc.tcl ...
# ==================================================
set MY_M [expr {[info exists ::env(ARRAY_M)] ? $::env(ARRAY_M) : 4}]
set MY_N [expr {[info exists ::env(ARRAY_N)] ? $::env(ARRAY_N) : 4}]

puts "=================================================="
puts "  Synthesizing with ARRAY_M=$MY_M, ARRAY_N=$MY_N"
puts "=================================================="

# ==================================================
# Sanity checks for injected Tcl variables
# These are expected to be set via dc_shell -x "set ..."
# ==================================================
foreach v {DESIGN PDK RTL_FILES_RAW SDC_FILE OUT_NETLIST CLK_FREQ_MHZ LIB_DBS_RAW} {
  if {![info exists $v]} {
    puts stderr "ERROR: variable $v is not set. Inject via dc_shell -x \"set $v ...\""
    exit 2
  }
}

set RESULT_DIR [file dirname $OUT_NETLIST]
file mkdir $RESULT_DIR

# ==================================================
# Library setup
# ==================================================
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

# search_path: include current dir and all db dirs
set sp [list .]
foreach f $lib_db_list {
  lappend sp [file dirname $f]
}

set_app_var search_path    $sp
set_app_var target_library $lib_db_list
set_app_var link_library   [concat "*" $target_library]

# ==================================================
# Read RTL
# ==================================================
set rtl_list [regexp -all -inline {\S+} $RTL_FILES_RAW]

if {[llength $rtl_list] == 0} {
  puts stderr "ERROR: RTL_FILES_RAW is empty."
  exit 2
}

foreach f $rtl_list {
  if {![file exists $f]} {
    puts stderr "ERROR: RTL file not found: $f"
    exit 2
  }
}

analyze -format sverilog $rtl_list
elaborate $DESIGN -parameters "ARRAY_M=$MY_M, ARRAY_N=$MY_N"
current_design $DESIGN
link

# ==================================================
# Constraints
# ==================================================
if {![file exists $SDC_FILE]} {
  puts stderr "ERROR: SDC_FILE not found: $SDC_FILE"
  exit 2
}
source $SDC_FILE

# Optional: if SDC didn't create clocks, you may enforce one
# set CLK_PERIOD_NS [expr {1000.0 / $CLK_FREQ_MHZ}]
# if {[sizeof_collection [get_clocks *]] == 0} {
#   puts "Warning: No clocks found. Creating default clock on port 'clock'."
#   create_clock -period $CLK_PERIOD_NS [get_ports clock]
# }

# ==================================================
# Basic design checks before compile
# ==================================================
check_design > $RESULT_DIR/dc.pre_compile.check_design.rpt

# ==================================================
# Compile
# ==================================================
set compile_seqmap_propagate_constants true
set_fix_multiple_port_nets -all -buffer_constants
compile_ultra

# ==================================================
# Reports
# ==================================================
report_qor                              > $RESULT_DIR/dc.qor.rpt
report_area                             > $RESULT_DIR/dc.area.rpt
report_timing -max_paths 50 -delay_type max > $RESULT_DIR/dc.timing.rpt
report_clock -attributes                > $RESULT_DIR/dc.clock.rpt
check_design                            > $RESULT_DIR/dc.check_design.rpt

# ==================================================
# Write outputs
# ==================================================
write -format verilog -hierarchy -output $OUT_NETLIST
# write_sdc $RESULT_DIR/${DESIGN}.dc_out.sdc

exit