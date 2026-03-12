#!/bin/bash

design="tripim_base_die_top"
sdc_file="./constraint.sdc"
rtl_files=$(find ./vsrc -type f \( -name "*.v" -o -name "*.sv" -o -name "*.vh" \) | tr '\n' ' ')
PDK=asap7sc7p5t_28
STA_TOOL=pt

# dims=(2 4 8 16 32 64 128)
dims=(16 32 64 128)
# freqs=(500 600 700 800 900 1100 1200 1300 1400 1500)
freqs=(1000)
max_jobs=10

run_one() {
    local dim=$1
    local freq=$2

    local result_dir="result/${design}-${PDK}-${freq}MHz-M${dim}-N${dim}"
    mkdir -p "$result_dir"

    local syn_log="${result_dir}/syn.log"
    local sta_log="${result_dir}/sta.log"

    echo "Starting ARRAY_M=$dim ARRAY_N=$dim CLK=${freq}MHz"

    ARRAY_M=$dim ARRAY_N=$dim \
    make syn \
        PDK="$PDK" \
        DESIGN="$design" \
        SDC_FILE="$sdc_file" \
        RTL_FILES="$rtl_files" \
        CLK_FREQ_MHZ="$freq" \
        > "$syn_log" 2>&1

    ARRAY_M=$dim ARRAY_N=$dim \
    make sta \
        PDK="$PDK" \
        DESIGN="$design" \
        SDC_FILE="$sdc_file" \
        RTL_FILES="$rtl_files" \
        CLK_FREQ_MHZ="$freq" \
        STA_TOOL="$STA_TOOL" \
        > "$sta_log" 2>&1

    echo "Finished ARRAY_M=$dim ARRAY_N=$dim CLK=${freq}MHz"
}

for dim in "${dims[@]}"; do
    for freq in "${freqs[@]}"; do
        run_one "$dim" "$freq" &

        while [ "$(jobs -r | wc -l)" -ge "$max_jobs" ]; do
            sleep 1
        done
    done
done

wait
echo "All runs finished."