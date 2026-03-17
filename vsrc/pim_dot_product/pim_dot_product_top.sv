module pim_dot_product_top #(
    parameter int DATA_W       = 1024,
    parameter int ELEM_W       = 8,
    parameter int LANES        = DATA_W / ELEM_W,
    parameter bit SIGNED_ELEM  = 1,
    parameter int PROD_W       = 2 * ELEM_W,
    parameter int ACC_W        = PROD_W + $clog2(LANES),
    parameter bit PIPELINE_MUL = 0,
    parameter bit PIPELINE_ADD = 0
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic [DATA_W-1:0]        vec_a,
    input  logic [DATA_W-1:0]        vec_b,
    output logic                     out_valid,
    input  logic                     out_ready,
    output logic signed [ACC_W-1:0]  out_sum
);

    dot_product_array #(
        .DATA_W      (DATA_W),
        .ELEM_W      (ELEM_W),
        .LANES       (LANES),
        .SIGNED_ELEM (SIGNED_ELEM),
        .PROD_W      (PROD_W),
        .ACC_W       (ACC_W),
        .PIPELINE_MUL(PIPELINE_MUL),
        .PIPELINE_ADD(PIPELINE_ADD)
    ) u_dot_product_array (
        .clk      (clk),
        .rst_n    (rst_n),
        .in_valid (in_valid),
        .in_ready (in_ready),
        .vec_a    (vec_a),
        .vec_b    (vec_b),
        .out_valid(out_valid),
        .out_ready(out_ready),
        .out_sum  (out_sum)
    );

endmodule
