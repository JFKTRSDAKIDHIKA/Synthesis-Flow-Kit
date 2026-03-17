#!/bin/bash

design="dot_product_array"
sdc_file="./constraint.sdc"
rtl_files=$(find ./vsrc/pim_dot_product -type f \( -name "*.v" -o -name "*.sv" -o -name "*.vh" \) | tr '\n' ' ')
PDK=asap7sc7p5t_28
STA_TOOL=pt

data_widths=(128 256 512 1024 2048)
freqs=(500 600 700 800 900 1000 1100 1200 1300 1400 1500)
max_jobs=10

run_one() {
    local data_w=$1
    local freq=$2

    local result_dir="result/${design}-${PDK}-${freq}MHz-DATA_W${data_w}"
    mkdir -p "$result_dir"

    local syn_log="${result_dir}/syn.log"
    local sta_log="${result_dir}/sta.log"

    echo "Starting DATA_W=$data_w CLK=${freq}MHz"

    DATA_W=$data_w \
    make syn \
        PDK="$PDK" \
        DESIGN="$design" \
        SDC_FILE="$sdc_file" \
        RTL_FILES="$rtl_files" \
        CLK_FREQ_MHZ="$freq" \
        RESULT_DIR="$result_dir" \
        > "$syn_log" 2>&1

    DATA_W=$data_w \
    make sta \
        PDK="$PDK" \
        DESIGN="$design" \
        SDC_FILE="$sdc_file" \
        RTL_FILES="$rtl_files" \
        CLK_FREQ_MHZ="$freq" \
        RESULT_DIR="$result_dir" \
        STA_TOOL="$STA_TOOL" \
        > "$sta_log" 2>&1

    echo "Finished DATA_W=$data_w CLK=${freq}MHz"
}

for data_w in "${data_widths[@]}"; do
    for freq in "${freqs[@]}"; do
        run_one "$data_w" "$freq" &

        while [ "$(jobs -r | wc -l)" -ge "$max_jobs" ]; do
            sleep 1
        done
    done
done

wait
echo "All runs finished."
