# Internal support for ordered design hooks and native database checkpoints.
proc flow_hook {phase stage} {
    set key FLOW_HOOK_[string toupper $phase]_[string toupper [string map {- _} $stage]]
    if {![info exists ::env($key)]} {return}
    foreach path [split $::env($key) "\n"] {
        if {$path eq ""} {continue}
        puts "CORAL_GENUS_HOOK_BEGIN $phase $stage $path"
        uplevel #0 [list source $path]
        puts "CORAL_GENUS_HOOK_END $phase $stage $path"
    }
}
proc flow_save_checkpoint {stage} {
    if {![info exists ::env(FLOW_SAVE_CHECKPOINTS)] || $stage ni $::env(FLOW_SAVE_CHECKPOINTS)} {return}
    set folder [file join $::env(FLOW_OUT) checkpoints $stage]
    file mkdir $folder
    write_db -all_root_attributes [file join $folder design.db]
    write_sdc > [file join $folder constraints.sdc]
    set stream [open [file join $folder tool-version.txt] w]
    puts $stream [get_db program_version]
    close $stream
    puts "CORAL_GENUS_CHECKPOINT_OK $stage"
}
