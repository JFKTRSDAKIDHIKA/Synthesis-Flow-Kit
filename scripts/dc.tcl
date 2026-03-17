# scripts/dc.tcl

# ==================================================
# Optional parameter override for DSE
# ==================================================
set param_overrides {}

foreach {param env_name} {
    ARRAY_M ARRAY_M
    ARRAY_N ARRAY_N
    DATA_W  DATA_W
} {
    if {[info exists ::env($env_name)]} {
        set param_value $::env($env_name)
        lappend param_overrides "${param}=${param_value}"
    }
}

if {[llength $param_overrides] > 0} {
    puts "Synthesizing with parameter overrides: [join $param_overrides {, }]"
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
if {[llength $param_overrides] > 0} {
    elaborate $DESIGN -parameters [join $param_overrides {, }]
} else {
    elaborate $DESIGN
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
