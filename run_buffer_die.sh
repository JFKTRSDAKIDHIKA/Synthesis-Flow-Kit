#!/bin/bash

design="tripim_buffer_die_top"
sdc_file="./constraint.sdc"
rtl_files=$(find ./vsrc -type f \( -name "*.v" -o -name "*.sv" -o -name "*.vh" \) | tr '\n' ' ')
PDK=asap7sc7p5t_28
STA_TOOL=pt
freq=1000

result_dir="result/${design}-${PDK}-${freq}MHz"
mkdir -p "$result_dir"

echo "Starting design=$design pdk=$pdk CLK=${freq}MHz"

make syn \
    PDK="$PDK" \
    DESIGN="$design" \
    SDC_FILE="$sdc_file" \
    RTL_FILES="$rtl_files" \
    CLK_FREQ_MHZ="$freq" 

make sta \
    PDK="$PDK" \
    DESIGN="$design" \
    SDC_FILE="$sdc_file" \
    RTL_FILES="$rtl_files" \
    CLK_FREQ_MHZ="$freq" \
    STA_TOOL="$STA_TOOL" 
