module tripim_noc_fabric #(
  parameter int NUM_SRES       = 8,
  parameter int SRC_FIFO_DEPTH = 4,
  parameter int DST_FIFO_DEPTH = 4
)(
  input  logic clk,
  input  logic rst_n,

  input  logic [NUM_SRES-1:0]                                tx_valid_i,
  output logic [NUM_SRES-1:0]                                tx_ready_o,
  input  logic [NUM_SRES-1:0][$bits(tripim_pkg::noc_pkt_t)-1:0] tx_pkt_i,

  output logic [NUM_SRES-1:0]                                rx_valid_o,
  input  logic [NUM_SRES-1:0]                                rx_ready_i,
  output logic [NUM_SRES-1:0][$bits(tripim_pkg::noc_pkt_t)-1:0] rx_pkt_o
);
  import tripim_pkg::*;

  localparam int NOC_PKT_W = $bits(noc_pkt_t);

  logic [NUM_SRES-1:0] srcq_in_ready;
  logic [NUM_SRES-1:0] srcq_out_valid;
  logic [NUM_SRES-1:0] srcq_out_ready;
  logic [NUM_SRES-1:0][NOC_PKT_W-1:0] srcq_out_bits;

  logic [NUM_SRES-1:0] dstq_in_valid;
  logic [NUM_SRES-1:0] dstq_in_ready;
  logic [NUM_SRES-1:0][NOC_PKT_W-1:0] dstq_in_bits;

  logic [NUM_SRES-1:0] dstq_out_valid;
  logic [NUM_SRES-1:0] dstq_out_ready;
  logic [NUM_SRES-1:0][NOC_PKT_W-1:0] dstq_out_bits;

  logic arb_valid;
  logic [$clog2(NUM_SRES)-1:0] arb_sel;
  logic [NOC_PKT_W-1:0] sel_pkt_bits;
  noc_pkt_t sel_pkt;

  logic [NUM_SRES-1:0] route_mask;
  logic route_fire;

  genvar g;
  generate
    for (g = 0; g < NUM_SRES; g++) begin : GEN_SRC_FIFO
      tripim_task_fifo #(
        .WIDTH(NOC_PKT_W),
        .DEPTH(SRC_FIFO_DEPTH)
      ) u_src_fifo (
        .clk      (clk),
        .rst_n    (rst_n),
        .in_valid (tx_valid_i[g]),
        .in_ready (srcq_in_ready[g]),
        .in_data  (tx_pkt_i[g]),
        .out_valid(srcq_out_valid[g]),
        .out_ready(srcq_out_ready[g]),
        .out_data (srcq_out_bits[g]),
        .level    ()
      );

      tripim_task_fifo #(
        .WIDTH(NOC_PKT_W),
        .DEPTH(DST_FIFO_DEPTH)
      ) u_dst_fifo (
        .clk      (clk),
        .rst_n    (rst_n),
        .in_valid (dstq_in_valid[g]),
        .in_ready (dstq_in_ready[g]),
        .in_data  (dstq_in_bits[g]),
        .out_valid(dstq_out_valid[g]),
        .out_ready(dstq_out_ready[g]),
        .out_data (dstq_out_bits[g]),
        .level    ()
      );
    end
  endgenerate

  assign tx_ready_o   = srcq_in_ready;
  assign rx_valid_o   = dstq_out_valid;
  assign dstq_out_ready = rx_ready_i;
  assign rx_pkt_o     = dstq_out_bits;

  integer i;
  always_comb begin
    arb_valid = 1'b0;
    arb_sel   = '0;
    for (i = 0; i < NUM_SRES; i++) begin
      if (!arb_valid && srcq_out_valid[i]) begin
        arb_valid = 1'b1;
        arb_sel   = i[$clog2(NUM_SRES)-1:0];
      end
    end
  end

  always_comb begin
    sel_pkt_bits = '0;
    for (i = 0; i < NUM_SRES; i++) begin
      if (arb_valid && (arb_sel == i[$clog2(NUM_SRES)-1:0])) begin
        sel_pkt_bits = srcq_out_bits[i];
      end
    end
  end
  assign sel_pkt = noc_pkt_t'(sel_pkt_bits);

  always_comb begin
    route_mask = '0;
    if (arb_valid) begin
      if (sel_pkt.bcast) begin
        route_mask = sel_pkt.bcast_mask[NUM_SRES-1:0];
      end else begin
        if (sel_pkt.dst_sre_id < NUM_SRES) route_mask[sel_pkt.dst_sre_id] = 1'b1;
      end
    end
  end

  always_comb begin
    route_fire = arb_valid;
    if (arb_valid) begin
      for (i = 0; i < NUM_SRES; i++) begin
        if (route_mask[i] && !dstq_in_ready[i]) route_fire = 1'b0;
      end
    end
  end

  always_comb begin
    srcq_out_ready = '0;
    dstq_in_valid  = '0;
    dstq_in_bits   = '0;

    if (arb_valid && route_fire) begin
      srcq_out_ready[arb_sel] = 1'b1;
      for (i = 0; i < NUM_SRES; i++) begin
        if (route_mask[i]) begin
          dstq_in_valid[i] = 1'b1;
          dstq_in_bits[i]  = sel_pkt_bits;
        end
      end
    end
  end
endmodule
