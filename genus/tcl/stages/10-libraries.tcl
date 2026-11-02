# Shared native synthesis stage; invoked by the snapshotted driver.
set libs [concat [list [need FLOW_STD_LIB]] [glob -nocomplain [file join [need FLOW_SRAM_LIB_DIR] *.lib]]]
set_db library $libs
