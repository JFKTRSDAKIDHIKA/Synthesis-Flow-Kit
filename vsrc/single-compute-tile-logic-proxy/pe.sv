module pe #(
    parameter int DATA_W = 16,
    parameter int ACC_W  = 32
) (
    input  logic                         clk,
    input  logic                         rst_n,
    input  logic                         clear_i,
    input  logic                         en_i,
    input  logic signed [DATA_W-1:0]     a_in,
    input  logic signed [DATA_W-1:0]     b_in,
    input  logic                         valid_in,
    output logic signed [DATA_W-1:0]     a_out,
    output logic signed [DATA_W-1:0]     b_out,
    output logic                         valid_out,
    output logic signed [ACC_W-1:0]      acc_out
);

    logic signed [ACC_W-1:0] acc_q;

    assign acc_out = acc_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            a_out     <= '0;
            b_out     <= '0;
            valid_out <= 1'b0;
            acc_q     <= '0;
        end else if (clear_i) begin
            a_out     <= '0;
            b_out     <= '0;
            valid_out <= 1'b0;
            acc_q     <= '0;
        end else if (en_i) begin
            a_out     <= a_in;
            b_out     <= b_in;
            valid_out <= valid_in;

            if (valid_in) begin
                acc_q <= acc_q + (a_in * b_in);
            end
        end
    end

endmodule
