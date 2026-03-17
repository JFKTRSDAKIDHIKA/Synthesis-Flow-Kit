module fifo_sync #(
    parameter int WIDTH = 64,
    parameter int DEPTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             push,
    input  logic             pop,
    input  logic [WIDTH-1:0] din,
    output logic [WIDTH-1:0] dout,
    output logic             full,
    output logic             empty
);

    localparam int PTR_W = (DEPTH <= 2) ? 1 : $clog2(DEPTH);

    logic [WIDTH-1:0] mem [0:DEPTH-1];
    logic [PTR_W-1:0] wr_ptr;
    logic [PTR_W-1:0] rd_ptr;
    logic [PTR_W:0]   count;

    logic do_push;
    logic do_pop;

    function automatic logic [PTR_W-1:0] ptr_inc(input logic [PTR_W-1:0] ptr);
        if (ptr == DEPTH-1) begin
            ptr_inc = '0;
        end else begin
            ptr_inc = ptr + 1'b1;
        end
    endfunction

    assign do_push = push && !full;
    assign do_pop  = pop  && !empty;

    assign full  = (count == DEPTH);
    assign empty = (count == 0);
    assign dout  = mem[rd_ptr];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= '0;
            rd_ptr <= '0;
            count  <= '0;
        end else begin
            if (do_push) begin
                mem[wr_ptr] <= din;
                wr_ptr      <= ptr_inc(wr_ptr);
            end

            if (do_pop) begin
                rd_ptr <= ptr_inc(rd_ptr);
            end

            case ({do_push, do_pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase
        end
    end

endmodule
