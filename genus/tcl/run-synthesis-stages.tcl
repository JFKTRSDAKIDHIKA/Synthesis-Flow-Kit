# Internal driver: stages execute in one Genus session; Python validates restarts.
set flow_tcl_dir [file dirname [file normalize [info script]]]
source [file join $flow_tcl_dir iteration-control.tcl]
set flow_resume_stage ""
if {[info exists ::env(FLOW_RESUME_STAGE)]} {set flow_resume_stage $::env(FLOW_RESUME_STAGE)}
foreach stage {
    00-setup 10-libraries 20-read-hdl 30-elaborate 40-constraints
    50-generic 60-map 70-optimize 80-reports 90-export
} {
    if {$flow_resume_stage ne "" && [string compare $stage 10-libraries]>0 && [string compare $stage $flow_resume_stage]<=0} {
        puts "CORAL_GENUS_STAGE_RESTORED $stage"
        continue
    }
    puts "CORAL_GENUS_STAGE_BEGIN $stage"
    flow_hook before $stage
    source [file join $flow_tcl_dir stages ${stage}.tcl]
    flow_hook after $stage
    if {$stage eq "10-libraries" && $flow_resume_stage ne ""} {
        set stream [open $::env(FLOW_RESUME_VERSION) r]
        set saved_version [string trim [read $stream]]
        close $stream
        if {$saved_version ne [get_db program_version]} {error "Checkpoint Genus version mismatch"}
        read_db $::env(FLOW_RESUME_DB)
        # Root attributes in the DB must not override this attempt's CPU/effort settings.
        set_db max_cpus [need FLOW_THREADS]
        foreach {attribute key} {syn_generic_effort GENERIC_EFFORT syn_map_effort MAP_EFFORT syn_opt_effort OPT_EFFORT} {
            set_db $attribute [flow_effort $key]
        }
        set restored_clock [get_db clocks core_clock]
        if {[llength $restored_clock]!=1 || abs([get_db $restored_clock .period]-[need FLOW_PERIOD_PS])>0.01} {
            error "Restored checkpoint clock mismatch"
        }
        check_design -unresolved > [file join $out reports restored-structure.rpt]
    }
    flow_save_checkpoint $stage
    puts "CORAL_GENUS_STAGE_END $stage"
}
puts "CORAL_GENUS_SYNTHESIS_OK"
exit
