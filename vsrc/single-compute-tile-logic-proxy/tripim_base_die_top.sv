module tripim_base_die_top #(
    parameter int DATA_W     = 16,
    parameter int ACC_W      = 32,
    parameter int ARRAY_M    = 16,
    parameter int ARRAY_N    = 16,
    parameter int TILE_K     = 4,
    parameter int FIFO_DEPTH = 8
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              start,
    output logic                              busy,
    output logic                              done,

    input  logic                              act_valid,
    input  logic [ARRAY_M*DATA_W-1:0]        act_data,
    output logic                              act_ready,

    output logic                              weight_req_o,
    output logic [$clog2(TILE_K)-1:0]         weight_k_idx_o,
    input  logic                              weight_valid_i,
    input  logic [ARRAY_N*DATA_W-1:0]        weight_data_i,

    output logic                              out_valid,
    input  logic                              out_ready,
    output logic [ARRAY_M*ARRAY_N*ACC_W-1:0] out_data
);

    compute_tile #(
        .DATA_W    (DATA_W),
        .ACC_W     (ACC_W),
        .ARRAY_M   (ARRAY_M),
        .ARRAY_N   (ARRAY_N),
        .TILE_K    (TILE_K),
        .FIFO_DEPTH(FIFO_DEPTH)
    ) u_compute_tile (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (start),
        .busy         (busy),
        .done         (done),
        .act_valid    (act_valid),
        .act_data     (act_data),
        .act_ready    (act_ready),
        .weight_req_o (weight_req_o),
        .weight_k_idx_o(weight_k_idx_o),
        .weight_valid_i(weight_valid_i),
        .weight_data_i(weight_data_i),
        .out_valid    (out_valid),
        .out_ready    (out_ready),
        .out_data     (out_data)
    );

endmodule
