#!/bin/bash

design="tripim_buffer_die_top"
sdc_file="./constraint.sdc"
rtl_files=$(find ./vsrc/tripim_buffer_die -type f \( -name "*.v" -o -name "*.sv" -o -name "*.vh" \) | tr '\n' ' ')
PDK=nangate45
STA_TOOL=pt
freqs=(100 200 300 400 500 600 700 800 900 1000)
max_jobs=10

run_one() {
    local freq=$1
    local result_dir="result/${design}-${PDK}-${freq}MHz"
    mkdir -p "$result_dir"

    local syn_log="${result_dir}/syn.log"
    local sta_log="${result_dir}/sta.log"

    echo "Starting design=$design PDK=$PDK CLK=${freq}MHz"

    make syn \
        PDK="$PDK" \
        DESIGN="$design" \
        SDC_FILE="$sdc_file" \
        RTL_FILES="$rtl_files" \
        CLK_FREQ_MHZ="$freq" \
        RESULT_DIR="$result_dir" \
        > "$syn_log" 2>&1

    make sta \
        PDK="$PDK" \
        DESIGN="$design" \
        SDC_FILE="$sdc_file" \
        RTL_FILES="$rtl_files" \
        CLK_FREQ_MHZ="$freq" \
        RESULT_DIR="$result_dir" \
        STA_TOOL="$STA_TOOL" \
        > "$sta_log" 2>&1

    echo "Finished design=$design PDK=$PDK CLK=${freq}MHz"
}

for freq in "${freqs[@]}"; do
    run_one "$freq" &

    while [ "$(jobs -r | wc -l)" -ge "$max_jobs" ]; do
        sleep 1
    done
done

wait
echo "All runs finished."
