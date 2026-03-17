module compute_tile #(
    parameter int DATA_W     = 16,
    parameter int ACC_W      = 32,
    parameter int ARRAY_M    = 4,
    parameter int ARRAY_N    = 4,
    parameter int TILE_K     = 4,
    parameter int FIFO_DEPTH = 8
) (
    input  logic                               clk,
    input  logic                               rst_n,
    input  logic                               start,
    output logic                               busy,
    output logic                               done,

    input  logic                               act_valid,
    input  logic [ARRAY_M*DATA_W-1:0]         act_data,
    output logic                               act_ready,

    output logic                               weight_req_o,
    output logic [$clog2(TILE_K)-1:0]          weight_k_idx_o,
    input  logic                               weight_valid_i,
    input  logic [ARRAY_N*DATA_W-1:0]         weight_data_i,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [ARRAY_M*ARRAY_N*ACC_W-1:0]  out_data
);

    localparam int FEED_W = (TILE_K <= 1) ? 1 : $clog2(TILE_K+1);

    logic                              fifo_push;
    logic                              fifo_pop;
    logic                              fifo_full;
    logic                              fifo_empty;
    logic [ARRAY_M*DATA_W-1:0]        fifo_dout;

    logic                              ctl_compute_active;
    logic                              ctl_sa_clear;
    logic                              ctl_sa_en;
    logic                              ctl_result_valid;

    logic [FEED_W-1:0]                 feed_cnt_q;
    logic                              need_feed;
    logic                              can_feed;
    logic                              sa_valid;
    logic                              step_i;

    logic [ARRAY_M*DATA_W-1:0]         sa_a_vec;
    logic [ARRAY_N*DATA_W-1:0]         sa_b_vec;
    logic [ARRAY_M*ARRAY_N*ACC_W-1:0]  sa_acc_vec;

    logic                               out_valid_q;
    logic [ARRAY_M*ARRAY_N*ACC_W-1:0]  out_data_q;

    assign fifo_push = act_valid && act_ready;
    assign act_ready = !fifo_full;

    assign need_feed = ctl_compute_active && (feed_cnt_q < TILE_K);
    assign weight_req_o = need_feed && !fifo_empty;
    assign weight_k_idx_o = feed_cnt_q[$clog2(TILE_K)-1:0];

    assign can_feed = need_feed && !fifo_empty && weight_valid_i;

    assign step_i = ctl_compute_active && (can_feed || !need_feed);
    assign sa_valid = can_feed;

    assign sa_a_vec = can_feed ? fifo_dout    : '0;
    assign sa_b_vec = can_feed ? weight_data_i : '0;
    assign fifo_pop = ctl_sa_en && can_feed;

    fifo_sync #(
        .WIDTH(ARRAY_M*DATA_W),
        .DEPTH(FIFO_DEPTH)
    ) u_act_fifo (
        .clk  (clk),
        .rst_n(rst_n),
        .push (fifo_push),
        .pop  (fifo_pop),
        .din  (act_data),
        .dout (fifo_dout),
        .full (fifo_full),
        .empty(fifo_empty)
    );

    tile_controller #(
        .TILE_K (TILE_K),
        .ARRAY_M(ARRAY_M),
        .ARRAY_N(ARRAY_N)
    ) u_ctrl (
        .clk             (clk),
        .rst_n           (rst_n),
        .start           (start),
        .step_i          (step_i),
        .busy_o          (busy),
        .done_o          (done),
        .compute_active_o(ctl_compute_active),
        .sa_clear_o      (ctl_sa_clear),
        .sa_en_o         (ctl_sa_en),
        .result_valid_o  (ctl_result_valid)
    );

    systolic_array #(
        .DATA_W (DATA_W),
        .ACC_W  (ACC_W),
        .ARRAY_M(ARRAY_M),
        .ARRAY_N(ARRAY_N)
    ) u_sa (
        .clk      (clk),
        .rst_n    (rst_n),
        .clear_i  (ctl_sa_clear),
        .en_i     (ctl_sa_en),
        .valid_i  (sa_valid),
        .a_vec_i  (sa_a_vec),
        .b_vec_i  (sa_b_vec),
        .acc_vec_o(sa_acc_vec)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            feed_cnt_q <= '0;
        end else if (ctl_sa_clear) begin
            feed_cnt_q <= '0;
        end else if (ctl_sa_en && can_feed) begin
            feed_cnt_q <= feed_cnt_q + 1'b1;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid_q <= 1'b0;
            out_data_q  <= '0;
        end else begin
            if (ctl_result_valid) begin
                out_data_q  <= sa_acc_vec;
                out_valid_q <= 1'b1;
            end else if (out_valid_q && out_ready) begin
                out_valid_q <= 1'b0;
            end
        end
    end

    assign out_valid = out_valid_q;
    assign out_data  = out_data_q;

endmodule
