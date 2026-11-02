# Internal block synthesis driver; launch through genus/run.py.
set flow_kind block
source [file join [file dirname [file normalize [info script]]] stage-driver.tcl]
