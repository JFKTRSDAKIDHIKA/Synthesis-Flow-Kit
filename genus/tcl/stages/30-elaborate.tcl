# Shared native synthesis stage; invoked by the snapshotted driver.
elaborate $top
if {$flow_kind eq "block"} {
  foreach name [split $::env(FLOW_PRESERVE_BLOCKS)] {
    if {$name eq ""} { continue }
    set mods [get_db design:$top .modules $name]
    # Unreachable child definitions may have been pruned during elaboration.
    if {[llength $mods] > 0} {
      set_db $mods .preserve true
      set_db $mods .ungroup_ok false
    }
  }
}
# Retain RTL module boundaries through generic synthesis and mapping.
if {$hierarchy eq "flat"} {
  # Explicit objects avoid relying on the tool's current hierarchy level.
  if {[llength [get_db hinsts]] > 0} { ungroup -flatten [get_db hinsts] }
  if {[llength [get_db hinsts]] > 0} { error "Flat test left unresolved hierarchy" }
  puts "CORAL_GENUS_FLAT_RTL_OK"
} else {
  set_db [get_db design:$top .modules *] .ungroup_ok false
}
check_design -unresolved > [file join $out reports elaboration.rpt]
