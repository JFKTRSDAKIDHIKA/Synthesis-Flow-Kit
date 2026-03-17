# Run example:
# lc_shell -f scripts/nangate45_lib_to_db.tcl \
#   -x "set LIB_FILE {/home/3dic/SynthFlow_Kit/pdk/nangate45/lib/Nangate45_typ.lib}; \
#       set OUT_DB {/home/3dic/SynthFlow_Kit/pdk/nangate45/lib/Nangate45_typ.db};"

if {![info exists LIB_FILE]} { puts stderr "ERROR: LIB_FILE not set"; exit 2 }
if {![info exists OUT_DB]}   { puts stderr "ERROR: OUT_DB not set"; exit 2 }

if {![file exists $LIB_FILE]} {
  puts stderr "ERROR: liberty file not found: $LIB_FILE"
  exit 2
}

file mkdir [file dirname $OUT_DB]
set_app_var sh_continue_on_error false

puts "INFO: reading liberty: $LIB_FILE"
read_lib $LIB_FILE

set libs_col [get_libs *]
set lib_names [get_object_name $libs_col]
if {[llength $lib_names] == 0} {
  puts stderr "ERROR: no libraries loaded from $LIB_FILE"
  exit 2
}

if {[llength $lib_names] > 1} {
  puts "INFO: loaded libraries:"
  foreach n $lib_names { puts "  $n" }
}

set lib_name [lindex $lib_names 0]
puts "INFO: write_lib -> $OUT_DB (library: $lib_name)"
write_lib -format db -output $OUT_DB $lib_name

puts "INFO: done"
exit
