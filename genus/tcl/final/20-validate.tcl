set clocks [get_db clocks core_clock]
if {[llength $clocks] != 1} {error "Missing/ambiguous core_clock"}
set actual [get_db $clocks .period]
if {abs($actual-$::env(FLOW_PERIOD_PS))>0.01} {error "Native clock period mismatch: $actual"}
foreach line [split $::env(FLOW_EXPECTED_MACROS) "\n"] {
    if {$line eq ""} {continue}
    lassign $line macro expected
    set actual [llength [get_db insts -if ".base_cell.name == $macro"]]
    if {$actual != $expected} {error "Macro count mismatch: $macro expected $expected found $actual"}
}
