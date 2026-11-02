# Shared native synthesis stage; invoked by the snapshotted driver.
# Genus Common UI. Genus constraints use ps and fF, regardless of Liberty units.
proc need {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    error "Required environment variable $name is missing"
  }
  return $::env($name)
}
set top [need FLOW_TOP]
set out [need FLOW_OUT]
# VME geometry uses elaboration-time constant real expressions.
set_db hdl_enable_real_support true
set hierarchy [need FLOW_HIERARCHY]
if {$hierarchy eq "flat"} {
  set_db auto_ungroup both
} else {
  set_db auto_ungroup none
}
set_db max_cpus [need FLOW_THREADS]
set_db syn_generic_effort [need FLOW_EFFORT]
set_db syn_map_effort [need FLOW_EFFORT]
set_db syn_opt_effort [need FLOW_EFFORT]
