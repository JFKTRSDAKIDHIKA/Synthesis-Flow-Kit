module tripim_completion_fifo #(
  parameter int WIDTH = 64,
  parameter int DEPTH = 16
)(
  input  logic clk,
  input  logic rst_n,

  input  logic             in_valid,
  output logic             in_ready,
  input  logic [WIDTH-1:0] in_data,

  output logic             out_valid,
  input  logic             out_ready,
  output logic [WIDTH-1:0] out_data,

  output logic [$clog2(DEPTH+1)-1:0] level
);
  tripim_task_fifo #(
    .WIDTH(WIDTH),
    .DEPTH(DEPTH)
  ) u_fifo (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (in_valid),
    .in_ready (in_ready),
    .in_data  (in_data),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_data (out_data),
    .level    (level)
  );
endmodule
