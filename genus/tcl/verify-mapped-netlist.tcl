# Fresh native restore of the assembled mapped hierarchy; no optimization.
set flow_tcl_dir [file dirname [file normalize [info script]]]
foreach stage {10-restore 20-validate 30-reports 40-export} {
    puts "CORAL_GENUS_FINAL_STAGE_BEGIN $stage"
    source [file join $flow_tcl_dir final ${stage}.tcl]
    puts "CORAL_GENUS_FINAL_STAGE_END $stage"
}
puts CORAL_GENUS_FINAL_CHECK_OK
exit
