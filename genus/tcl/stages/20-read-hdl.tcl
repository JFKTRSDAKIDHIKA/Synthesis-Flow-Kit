# Shared native synthesis stage; invoked by the snapshotted driver.
set defs [split [need FLOW_DEFINES]]
set read_args [list -sv]
foreach def $defs { if {$def ne ""} { lappend read_args -define $def } }
read_hdl {*}$read_args [list [need FLOW_BLACKBOXES] [need FLOW_RTL]]
if {$flow_kind eq "block" && $::env(FLOW_CHILD_NETLISTS) ne ""} {
  read_hdl -netlist [split $::env(FLOW_CHILD_NETLISTS) "\n"]
}
