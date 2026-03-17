module tile_controller #(
    parameter int TILE_K  = 4,
    parameter int ARRAY_M = 4,
    parameter int ARRAY_N = 4
) (
    input  logic clk,
    input  logic rst_n,
    input  logic start,
    input  logic step_i,
    output logic busy_o,
    output logic done_o,
    output logic compute_active_o,
    output logic sa_clear_o,
    output logic sa_en_o,
    output logic result_valid_o
);

    localparam int COMPUTE_CYCLES = TILE_K + ARRAY_M + ARRAY_N - 2;
    localparam int CNT_W = (COMPUTE_CYCLES <= 1) ? 1 : $clog2(COMPUTE_CYCLES);

    typedef enum logic [2:0] {
        S_IDLE,
        S_LOAD,
        S_COMPUTE,
        S_DRAIN,
        S_DONE
    } state_t;

    state_t state_q;
    state_t state_d;

    logic [CNT_W-1:0] compute_cnt_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q       <= S_IDLE;
            compute_cnt_q <= '0;
        end else begin
            state_q <= state_d;

            if (state_q == S_LOAD) begin
                compute_cnt_q <= '0;
            end else if ((state_q == S_COMPUTE) && step_i) begin
                if (compute_cnt_q == COMPUTE_CYCLES-1) begin
                    compute_cnt_q <= compute_cnt_q;
                end else begin
                    compute_cnt_q <= compute_cnt_q + 1'b1;
                end
            end
        end
    end

    always_comb begin
        state_d          = state_q;
        busy_o           = 1'b0;
        done_o           = 1'b0;
        compute_active_o = 1'b0;
        sa_clear_o       = 1'b0;
        sa_en_o          = 1'b0;
        result_valid_o   = 1'b0;

        case (state_q)
            S_IDLE: begin
                if (start) begin
                    state_d = S_LOAD;
                end
            end

            /* Clear accumulators and pipeline state for one cycle. */
            S_LOAD: begin
                busy_o     = 1'b1;
                sa_clear_o = 1'b1;
                state_d    = S_COMPUTE;
            end

            /*
             * Execute systolic steps.
             * A/B injection may stall until inputs are ready.
             * Counter advances only when step_i=1.
             */
            S_COMPUTE: begin
                busy_o           = 1'b1;
                compute_active_o = 1'b1;
                sa_en_o          = step_i;

                if (step_i && (compute_cnt_q == COMPUTE_CYCLES-1)) begin
                    state_d = S_DRAIN;
                end
            end

            /* One cycle to publish result vector. */
            S_DRAIN: begin
                busy_o         = 1'b1;
                result_valid_o = 1'b1;
                state_d        = S_DONE;
            end

            /* done_o pulses for one cycle. */
            S_DONE: begin
                done_o  = 1'b1;
                state_d = S_IDLE;
            end

            default: begin
                state_d = S_IDLE;
            end
        endcase
    end

endmodule
