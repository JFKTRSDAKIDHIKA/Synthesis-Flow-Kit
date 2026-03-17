module systolic_array #(
    parameter int DATA_W  = 16,
    parameter int ACC_W   = 32,
    parameter int ARRAY_M = 4,
    parameter int ARRAY_N = 4
) (
    input  logic                                  clk,
    input  logic                                  rst_n,
    input  logic                                  clear_i,
    input  logic                                  en_i,
    input  logic                                  valid_i,
    input  logic [ARRAY_M*DATA_W-1:0]            a_vec_i,
    input  logic [ARRAY_N*DATA_W-1:0]            b_vec_i,
    output logic [ARRAY_M*ARRAY_N*ACC_W-1:0]     acc_vec_o
);

    localparam int VPIPE_D = ARRAY_M + ARRAY_N - 2;

    logic signed [DATA_W-1:0] a_edge [0:ARRAY_M-1];
    logic signed [DATA_W-1:0] b_edge [0:ARRAY_N-1];

    logic signed [DATA_W-1:0] a_in_left [0:ARRAY_M-1];
    logic signed [DATA_W-1:0] b_in_top  [0:ARRAY_N-1];

    logic signed [DATA_W-1:0] a_skew [0:ARRAY_M-1][0:ARRAY_M-2];
    logic signed [DATA_W-1:0] b_skew [0:ARRAY_N-1][0:ARRAY_N-2];

    logic signed [DATA_W-1:0] a_link [0:ARRAY_M-1][0:ARRAY_N-1];
    logic signed [DATA_W-1:0] b_link [0:ARRAY_M-1][0:ARRAY_N-1];
    logic                     v_dummy [0:ARRAY_M-1][0:ARRAY_N-1];
    logic signed [ACC_W-1:0]  acc_pe [0:ARRAY_M-1][0:ARRAY_N-1];

    logic                     valid_pipe [1:VPIPE_D];

    genvar r, c, d;

    generate
        for (r = 0; r < ARRAY_M; r++) begin : UNPACK_A
            assign a_edge[r] = a_vec_i[r*DATA_W +: DATA_W];
        end

        for (c = 0; c < ARRAY_N; c++) begin : UNPACK_B
            assign b_edge[c] = b_vec_i[c*DATA_W +: DATA_W];
        end
    endgenerate

    /*
     * Boundary skewing:
     * - Row r of A is delayed by r cycles before entering column 0.
     * - Column c of B is delayed by c cycles before entering row 0.
     * This aligns k-dimension operands at every PE.
     */
    generate
        if (ARRAY_M > 1) begin : GEN_A_SKEW
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (int i = 0; i < ARRAY_M; i++) begin
                        for (int j = 0; j < ARRAY_M-1; j++) begin
                            a_skew[i][j] <= '0;
                        end
                    end
                end else if (clear_i) begin
                    for (int i = 0; i < ARRAY_M; i++) begin
                        for (int j = 0; j < ARRAY_M-1; j++) begin
                            a_skew[i][j] <= '0;
                        end
                    end
                end else if (en_i) begin
                    for (int i = 0; i < ARRAY_M; i++) begin
                        a_skew[i][0] <= a_edge[i];
                        for (int j = 1; j < ARRAY_M-1; j++) begin
                            a_skew[i][j] <= a_skew[i][j-1];
                        end
                    end
                end
            end
        end

        if (ARRAY_N > 1) begin : GEN_B_SKEW
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (int i = 0; i < ARRAY_N; i++) begin
                        for (int j = 0; j < ARRAY_N-1; j++) begin
                            b_skew[i][j] <= '0;
                        end
                    end
                end else if (clear_i) begin
                    for (int i = 0; i < ARRAY_N; i++) begin
                        for (int j = 0; j < ARRAY_N-1; j++) begin
                            b_skew[i][j] <= '0;
                        end
                    end
                end else if (en_i) begin
                    for (int i = 0; i < ARRAY_N; i++) begin
                        b_skew[i][0] <= b_edge[i];
                        for (int j = 1; j < ARRAY_N-1; j++) begin
                            b_skew[i][j] <= b_skew[i][j-1];
                        end
                    end
                end
            end
        end
    endgenerate

    generate
        for (r = 0; r < ARRAY_M; r++) begin : GEN_A_IN
            if (r == 0) begin
                assign a_in_left[r] = a_edge[r];
            end else begin
                assign a_in_left[r] = a_skew[r][r-1];
            end
        end

        for (c = 0; c < ARRAY_N; c++) begin : GEN_B_IN
            if (c == 0) begin
                assign b_in_top[c] = b_edge[c];
            end else begin
                assign b_in_top[c] = b_skew[c][c-1];
            end
        end
    endgenerate

    if (VPIPE_D > 0) begin : GEN_VALID_PIPE
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                for (int i = 1; i <= VPIPE_D; i++) begin
                    valid_pipe[i] <= 1'b0;
                end
            end else if (clear_i) begin
                for (int i = 1; i <= VPIPE_D; i++) begin
                    valid_pipe[i] <= 1'b0;
                end
            end else if (en_i) begin
                valid_pipe[1] <= valid_i;
                for (int i = 2; i <= VPIPE_D; i++) begin
                    valid_pipe[i] <= valid_pipe[i-1];
                end
            end
        end
    end

    /*
     * Per en_i cycle:
     * - A moves left->right one PE hop.
     * - B moves top->down one PE hop.
     * - Valid at PE(r,c) is valid_i delayed by (r+c) cycles.
     */
    generate
        for (r = 0; r < ARRAY_M; r++) begin : GEN_ROW
            for (c = 0; c < ARRAY_N; c++) begin : GEN_COL
                logic signed [DATA_W-1:0] a_in_w;
                logic signed [DATA_W-1:0] b_in_w;
                logic                     v_in_w;

                if (c == 0) begin
                    assign a_in_w = a_in_left[r];
                end else begin
                    assign a_in_w = a_link[r][c-1];
                end

                if (r == 0) begin
                    assign b_in_w = b_in_top[c];
                end else begin
                    assign b_in_w = b_link[r-1][c];
                end

                if ((r + c) == 0) begin
                    assign v_in_w = valid_i;
                end else begin
                    assign v_in_w = valid_pipe[r+c];
                end

                pe #(
                    .DATA_W(DATA_W),
                    .ACC_W (ACC_W)
                ) u_pe (
                    .clk      (clk),
                    .rst_n    (rst_n),
                    .clear_i  (clear_i),
                    .en_i     (en_i),
                    .a_in     (a_in_w),
                    .b_in     (b_in_w),
                    .valid_in (v_in_w),
                    .a_out    (a_link[r][c]),
                    .b_out    (b_link[r][c]),
                    .valid_out(v_dummy[r][c]),
                    .acc_out  (acc_pe[r][c])
                );

                assign acc_vec_o[(r*ARRAY_N + c)*ACC_W +: ACC_W] = acc_pe[r][c];
            end
        end
    endgenerate

endmodule
