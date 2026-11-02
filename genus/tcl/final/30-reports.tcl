check_design -unresolved -multiple_driver -combo_loops > [file join $out native-structure.rpt]
check_timing_intent > [file join $out timing-intent.rpt]
report_clocks > [file join $out clocks.rpt]
report_area > [file join $out area.rpt]
report_timing -max_paths 100 -nworst 1 > [file join $out setup.rpt]
# Hold reporting runs next in the separate OpenSTA min-delay stage.
puts "CORAL_GENUS_HOLD_DEFERRED_TO_OPENSTA"
