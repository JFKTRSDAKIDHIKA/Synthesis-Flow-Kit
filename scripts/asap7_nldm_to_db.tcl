# Run example:
# lc_shell -f scripts/asap7_nldm_to_db.tcl \
#   -x "set IN_DIR {/home/3dic/ls-sta/pdk/asap7sc7p5t_28/LIB/NLDM}; \
#       set OUT_DIR {/home/3dic/ls-sta/pdk/asap7sc7p5t_28/LIB/DB}; \
#       set PVT {RVT_TT};"

if {![info exists IN_DIR]}  { puts stderr "ERROR: IN_DIR not set";  exit 2 }
if {![info exists OUT_DIR]} { puts stderr "ERROR: OUT_DIR not set"; exit 2 }
if {![info exists PVT]}     { set PVT "RVT_TT" }

file mkdir $OUT_DIR
set_app_var sh_continue_on_error false

# Minimal set for synthesis
set patterns [list \
  "*SIMPLE_${PVT}_nldm_*.lib" \
  "*INVBUF_${PVT}_nldm_*.lib" \
  "*AO_${PVT}_nldm_*.lib" \
  "*OA_${PVT}_nldm_*.lib" \
  "*SEQ_${PVT}_nldm_*.lib" \
]

set lib_files {}
foreach p $patterns {
  set hits [glob -nocomplain -directory $IN_DIR $p]
  foreach h $hits { lappend lib_files $h }
}
if {[llength $lib_files] == 0} {
  puts stderr "ERROR: no lib files matched in $IN_DIR for PVT=$PVT"
  exit 2
}

puts "INFO: reading libs:"
foreach f $lib_files { puts "  $f" }

# Read libraries
foreach f $lib_files { read_lib $f }

# Convert collection -> Tcl list of strings (library names)
set libs_col [get_libs *]
set lib_names [get_object_name $libs_col]
puts "INFO: loaded libs:"
foreach n $lib_names { puts "  $n" }

# Write each library to a db file
foreach n $lib_names {
  set out_db [file join $OUT_DIR "${n}.db"]
  puts "INFO: write_lib -> $out_db"
  write_lib -format db -output $out_db $n
}

puts "INFO: done. db files are in $OUT_DIR"
exit
