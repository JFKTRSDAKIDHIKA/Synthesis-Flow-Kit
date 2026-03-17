(* black_box *)
module tripim_local_buffer #(
  parameter int DATA_W       = 64,
  parameter int BUFFER_BYTES = 4096,
  parameter int ADDR_W       = 9
) (
  input  logic              clk,
  input  logic              wr_en,
  input  logic [ADDR_W-1:0] wr_addr,
  input  logic [DATA_W-1:0] wr_data,
  input  logic [ADDR_W-1:0] rd_addr,
  output logic [DATA_W-1:0] rd_data
);
  // Intentionally left blank. This local buffer is modeled as a memory macro /
  // black box so synthesis does not map it into standard cells.
endmodule
