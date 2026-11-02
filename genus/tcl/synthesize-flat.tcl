# Internal flat synthesis driver; launch through genus/run.py.
set flow_kind flat
source [file join [file dirname [file normalize [info script]]] run-synthesis-stages.tcl]
