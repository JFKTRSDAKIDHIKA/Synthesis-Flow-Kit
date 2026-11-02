# Internal driver: each stage executes in the same Genus session.
set flow_tcl_dir [file dirname [file normalize [info script]]]
foreach stage {
    00-setup 10-libraries 20-read-hdl 30-elaborate 40-constraints
    50-generic 60-map 70-optimize 80-reports 90-export
} {
    puts "CORAL_GENUS_STAGE_BEGIN $stage"
    source [file join $flow_tcl_dir stages ${stage}.tcl]
    puts "CORAL_GENUS_STAGE_END $stage"
}
puts "CORAL_GENUS_SYNTHESIS_OK"
exit
