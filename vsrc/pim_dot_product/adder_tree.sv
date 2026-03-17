module adder_tree #(
    parameter int IN_COUNT     = 128,
    parameter int IN_W         = 16,
    parameter int OUT_W        = 23,
    parameter bit PIPELINE_ADD = 0
) (
    input  logic                          clk,
    input  logic                          rst_n,
    input  logic                          en_i,
    input  logic signed [IN_W-1:0]        in_data [IN_COUNT],
    output logic signed [OUT_W-1:0]       out_sum
);

    localparam int LEVELS = (IN_COUNT <= 1) ? 0 : $clog2(IN_COUNT);

    typedef logic signed [OUT_W-1:0] sum_t;

    sum_t stage_data [0:LEVELS][0:IN_COUNT-1];

    function automatic int level_count(input int level);
        int count_v;
        int idx;
        begin
            count_v = IN_COUNT;
            for (idx = 0; idx < level; idx++) begin
                count_v = (count_v + 1) / 2;
            end
            return count_v;
        end
    endfunction

    genvar in_idx;
    generate
        for (in_idx = 0; in_idx < IN_COUNT; in_idx++) begin : gen_input_ext
            assign stage_data[0][in_idx] = sum_t'(in_data[in_idx]);
        end

        for (genvar level = 0; level < LEVELS; level++) begin : gen_levels
            localparam int CUR_COUNT  = level_count(level);
            localparam int NEXT_COUNT = level_count(level + 1);

            for (genvar node = 0; node < NEXT_COUNT; node++) begin : gen_nodes
                localparam int LHS_IDX = 2 * node;
                localparam int RHS_IDX = (2 * node) + 1;

                sum_t pair_sum;

                // Balanced reduction tree. For odd fan-in at a given level,
                // the last element bypasses directly to the next level.
                if (RHS_IDX < CUR_COUNT) begin : gen_pair
                    assign pair_sum = stage_data[level][LHS_IDX] + stage_data[level][RHS_IDX];
                end else begin : gen_passthrough
                    assign pair_sum = stage_data[level][LHS_IDX];
                end

                if (PIPELINE_ADD) begin : gen_pipe
                    always_ff @(posedge clk or negedge rst_n) begin
                        if (!rst_n) begin
                            stage_data[level + 1][node] <= '0;
                        end else if (en_i) begin
                            stage_data[level + 1][node] <= pair_sum;
                        end
                    end
                end else begin : gen_comb
                    assign stage_data[level + 1][node] = pair_sum;
                end
            end

            for (genvar pad = NEXT_COUNT; pad < IN_COUNT; pad++) begin : gen_pad
                assign stage_data[level + 1][pad] = '0;
            end
        end
    endgenerate

    assign out_sum = stage_data[LEVELS][0];

endmodule
