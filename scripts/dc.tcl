# scripts/dc.tcl

# Sanity checks
foreach v {DESIGN PDK RTL_FILES_RAW SDC_FILE OUT_NETLIST CLK_FREQ_MHZ LIB_DBS_RAW} {
  if {![info exists $v]} {
    puts stderr "ERROR: variable $v is not set. Inject via dc_shell -x \"set $v ...\""
    exit 2
  }
}

set RESULT_DIR [file dirname $OUT_NETLIST]
file mkdir $RESULT_DIR

# Library setup
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

# search_path: include all db dirs
set sp [list .]
foreach f $lib_db_list { lappend sp [file dirname $f] }
set_app_var search_path $sp

set_app_var target_library $lib_db_list
set_app_var link_library   [concat "*" $target_library]

# Read RTL
set rtl_list [regexp -all -inline {\S+} $RTL_FILES_RAW]

if {[llength $rtl_list] == 0} {
  puts stderr "ERROR: RTL_FILES is empty."
  exit 2
}

foreach f $rtl_list {
  if {![file exists $f]} {
    puts stderr "ERROR: RTL file not found: $f"
    exit 2
  }
}

analyze -format sverilog $rtl_list
elaborate $DESIGN
current_design $DESIGN
link


# Constraints
if {![file exists $SDC_FILE]} {
  puts stderr "ERROR: SDC_FILE not found: $SDC_FILE"
  exit 2
}
source $SDC_FILE

# Optional: if SDC didn't create clocks, you may enforce one
# set CLK_PERIOD_NS [expr 1000.0 / $CLK_FREQ_MHZ]
# if {[sizeof_collection [get_clocks *]] == 0} {
#   puts "Warning: No clocks found. Creating default clock on port 'clock'."
#   create_clock -period $CLK_PERIOD_NS [get_ports clock]
# }


# Compile
set compile_seqmap_propagate_constants true
set_fix_multiple_port_nets -all -buffer_constants
compile_ultra
# compile -map_effort low

# Reports 
report_qor    > $RESULT_DIR/dc.qor.rpt
report_area   > $RESULT_DIR/dc.area.rpt
report_timing -max_paths 50 -delay_type max > $RESULT_DIR/dc.timing.rpt
report_clock -attributes > $RESULT_DIR/dc.clock.rpt
check_design            > $RESULT_DIR/dc.check_design.rpt

# Write outputs
write -format verilog -hierarchy -output $OUT_NETLIST
# write_sdc $RESULT_DIR/${DESIGN}.dc_out.sdc

exit
