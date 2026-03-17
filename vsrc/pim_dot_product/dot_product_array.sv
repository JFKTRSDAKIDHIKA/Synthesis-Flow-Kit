module dot_product_array #(
    parameter int DATA_W          = 1024,
    parameter int ELEM_W          = 8,
    parameter int LANES           = DATA_W / ELEM_W,
    parameter bit SIGNED_ELEM     = 1,
    parameter int PROD_W          = 2 * ELEM_W,
    parameter int ACC_W           = PROD_W + $clog2(LANES),
    parameter bit PIPELINE_MUL    = 0,
    parameter bit PIPELINE_ADD    = 0
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

    // This block is fully pipelined internally. Backpressure is handled by
    // freezing all pipeline registers with a shared advance enable; there is
    // no extra input skid buffer, so in_ready deasserts immediately when the
    // output side stalls.
    localparam int DERIVED_LANES = DATA_W / ELEM_W;
    localparam int TREE_LEVELS   = (LANES <= 1) ? 0 : $clog2(LANES);
    localparam int ADD_STAGES    = PIPELINE_ADD ? TREE_LEVELS : 0;
    localparam int TOTAL_STAGES  = (PIPELINE_MUL ? 1 : 0) + ADD_STAGES + 1;

    typedef logic signed [PROD_W-1:0] prod_t;

    logic [ELEM_W-1:0]          a_lane [LANES];
    logic [ELEM_W-1:0]          b_lane [LANES];
    prod_t                      product_comb [LANES];
    prod_t                      product_pipe [LANES];
    prod_t                      tree_in [LANES];
    logic signed [ACC_W-1:0]    tree_sum;
    logic [TOTAL_STAGES-1:0]    valid_pipe_q;
    logic signed [ACC_W-1:0]    out_sum_q;
    logic                       advance_pipe;
    logic                       in_fire;

    function automatic prod_t lane_product(
        input logic [ELEM_W-1:0] a_val,
        input logic [ELEM_W-1:0] b_val
    );
        logic signed [PROD_W:0] unsigned_prod;
        begin
            if (SIGNED_ELEM) begin
                lane_product = prod_t'($signed(a_val) * $signed(b_val));
            end else begin
                unsigned_prod = $signed({1'b0, a_val}) * $signed({1'b0, b_val});
                lane_product  = prod_t'(unsigned_prod[PROD_W-1:0]);
            end
        end
    endfunction

    generate
        if ((DATA_W % ELEM_W) != 0) begin : gen_bad_data_ratio
            PARAM_DATA_W_MUST_BE_MULTIPLE_OF_ELEM_W u_param_error();
        end

        if (LANES != DERIVED_LANES) begin : gen_bad_lane_cfg
            PARAM_LANES_MUST_MATCH_DATA_W_OVER_ELEM_W u_param_error();
        end

        if (LANES <= 0) begin : gen_bad_lane_zero
            PARAM_LANES_MUST_BE_POSITIVE u_param_error();
        end

        if (ACC_W < (PROD_W + $clog2(LANES))) begin : gen_bad_acc_w
            PARAM_ACC_W_TOO_SMALL u_param_error();
        end
    endgenerate

    assign advance_pipe = !out_valid || out_ready;
    assign in_ready     = advance_pipe;
    assign in_fire      = in_valid && in_ready;

    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane++) begin : gen_lanes
            // Slice the packed input buses into per-lane elements.
            assign a_lane[lane]      = vec_a[(lane * ELEM_W) +: ELEM_W];
            assign b_lane[lane]      = vec_b[(lane * ELEM_W) +: ELEM_W];
            assign product_comb[lane] = lane_product(a_lane[lane], b_lane[lane]);

            if (PIPELINE_MUL) begin : gen_mul_pipe
                always_ff @(posedge clk or negedge rst_n) begin
                    if (!rst_n) begin
                        product_pipe[lane] <= '0;
                    end else if (advance_pipe) begin
                        product_pipe[lane] <= product_comb[lane];
                    end
                end
            end else begin : gen_mul_comb
                assign product_pipe[lane] = product_comb[lane];
            end

            assign tree_in[lane] = product_pipe[lane];
        end
    endgenerate

    adder_tree #(
        .IN_COUNT    (LANES),
        .IN_W        (PROD_W),
        .OUT_W       (ACC_W),
        .PIPELINE_ADD(PIPELINE_ADD)
    ) u_adder_tree (
        .clk    (clk),
        .rst_n  (rst_n),
        .en_i   (advance_pipe),
        .in_data(tree_in),
        .out_sum(tree_sum)
    );

    // valid_pipe_q tracks the fixed datapath latency:
    //   input -> optional mul reg -> optional adder-tree regs -> output reg
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_pipe_q <= '0;
            out_sum_q    <= '0;
        end else if (advance_pipe) begin
            valid_pipe_q[0] <= in_fire;
            for (int stage = 1; stage < TOTAL_STAGES; stage++) begin
                valid_pipe_q[stage] <= valid_pipe_q[stage - 1];
            end
            out_sum_q <= tree_sum;
        end
    end

    assign out_valid = valid_pipe_q[TOTAL_STAGES - 1];
    assign out_sum   = out_sum_q;

endmodule
