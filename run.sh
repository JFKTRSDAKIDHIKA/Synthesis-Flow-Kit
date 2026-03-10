#!/bin/bash

# Set parameters
design="tripim_base_die_top"
sdc_file="./constraint.sdc"
rtl_files=$(find ./vsrc -type f \( -name "*.v" -o -name "*.sv" -o -name "*.vh" \) | tr '\n' ' ')
clk_freq_mhz=1000
# PDK=nangate45
PDK=asap7sc7p5t_28
STA_TOOL=pt

# Run make command
make syn PDK="$PDK" DESIGN="$design" SDC_FILE="$sdc_file" RTL_FILES="$rtl_files" CLK_FREQ_MHZ="$clk_freq_mhz"
make sta PDK="$PDK" DESIGN="$design" SDC_FILE="$sdc_file" RTL_FILES="$rtl_files" CLK_FREQ_MHZ="$clk_freq_mhz"
