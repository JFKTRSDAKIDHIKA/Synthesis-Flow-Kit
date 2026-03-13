# scripts/dc.tcl

# ==================================================
# Optional parameter override for DSE
# ==================================================
if {[info exists ::env(ARRAY_M)] && [info exists ::env(ARRAY_N)]} {
    set MY_M $::env(ARRAY_M)
    set MY_N $::env(ARRAY_N)
    puts "Synthesizing with ARRAY_M=$MY_M, ARRAY_N=$MY_N"
}

# ==================================================
# Result directory
# ==================================================
set RESULT_DIR [file dirname $OUT_NETLIST]
file mkdir $RESULT_DIR

# ==================================================
# Library setup
# ==================================================
set lib_db_list [split $LIB_DBS_RAW " "]

set search_path_list [list .]
foreach f $lib_db_list {
    lappend search_path_list [file dirname $f]
}

set_app_var search_path    $search_path_list
set_app_var target_library $lib_db_list
set_app_var link_library   [concat "*" $target_library]

# ==================================================
# Read RTL
# ==================================================
set rtl_list [regexp -all -inline {\S+} $RTL_FILES_RAW]

set pkg_list   {}
set other_list {}

foreach f $rtl_list {
    if {[regexp {(^|/)(tripim_pkg)\.(sv|svh)$} $f]} {
        lappend pkg_list $f
    } else {
        lappend other_list $f
    }
}

if {[llength $pkg_list] > 0} {
    puts "Analyzing package files first..."
    analyze -format sverilog $pkg_list
}

puts "Analyzing remaining RTL files..."
analyze -format sverilog $other_list

# ==================================================
# Elaborate and link
# ==================================================
if {$DESIGN eq "tripim_buffer_die_top"} {
    elaborate $DESIGN
} else {
    elaborate $DESIGN -parameters "ARRAY_M=$MY_M, ARRAY_N=$MY_N"
}

current_design $DESIGN
link

# ==================================================
# Constraints
# ==================================================
source $SDC_FILE

# ==================================================
# Compile
# ==================================================
check_design > $RESULT_DIR/dc.pre_compile.check_design.rpt

set compile_seqmap_propagate_constants true
set_fix_multiple_port_nets -all -buffer_constants
compile_ultra

# ==================================================
# Reports
# ==================================================
report_qor                                  > $RESULT_DIR/dc.qor.rpt
report_area                                 > $RESULT_DIR/dc.area.rpt
report_timing -max_paths 50 -delay_type max > $RESULT_DIR/dc.timing.rpt
report_clock -attributes                    > $RESULT_DIR/dc.clock.rpt
check_design                                > $RESULT_DIR/dc.check_design.rpt

# ==================================================
# Write outputs
# ==================================================
write -format verilog -hierarchy -output $OUT_NETLIST

exit