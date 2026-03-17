module tripim_buffer_die_top #(
  parameter int NUM_SRES                = 16,
  parameter int DATA_W                  = 64,
  parameter int TASK_QUEUE_DEPTH        = 8,
  parameter int COMPLETION_QUEUE_DEPTH  = 16,
  parameter int BUFFER_BYTES            = 4096,
  parameter int DMA_CMD_FIFO_DEPTH      = 8,
  parameter int DMA_ADDR_W              = 12
)(
  input  logic clk,
  input  logic rst_n,

  input  logic                   cmd_valid,
  output logic                   cmd_ready,
  input  tripim_pkg::task_desc_t cmd_desc,

  input  logic                                hb_in_valid,
  output logic                                hb_in_ready,
  input  logic [DATA_W-1:0]                   hb_in_data,
  input  logic                                hb_in_last,
  input  logic [tripim_pkg::TASK_ID_W-1:0]    hb_task_id,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]   hb_group_id,
  input  tripim_pkg::opcode_e                 hb_opcode,
  input  logic [tripim_pkg::SRE_ID_W-1:0]     hb_dst_sre_id,

  output logic                              done_valid,
  input  logic                              done_ready,
  output logic [tripim_pkg::TASK_ID_W-1:0]  done_task_id,
  output tripim_pkg::opcode_e               done_opcode,
  output logic [tripim_pkg::TAG_W-1:0]      done_tag,
  output logic [tripim_pkg::STATUS_W-1:0]   done_status
);
  import tripim_pkg::*;

  localparam int NOC_PKT_W = $bits(noc_pkt_t);
  localparam int NUM_GROUPS = 1;

  logic [NUM_SRES-1:0] sre_task_valid, sre_task_ready;
  task_desc_t          sre_task_desc [0:NUM_SRES-1];

  logic dma_task_valid, dma_task_ready;
  task_desc_t dma_task_desc;

  logic fe_done_valid, fe_done_ready;
  completion_event_t fe_done_event;

  logic grp_create_valid, grp_create_ready, grp_create_softmax_mode;
  logic [GROUP_ID_W-1:0] grp_create_gid;
  logic [MAX_SRES-1:0] grp_create_mask;
  logic [SRE_ID_W-1:0] grp_create_master;
  logic [TILE_CNT_W-1:0] grp_create_expected;

  logic [NUM_SRES-1:0] grp_q_valid;
  logic [NUM_SRES-1:0][GROUP_ID_W-1:0] grp_q_gid;
  logic [NUM_SRES-1:0] grp_q_hit;
  logic [NUM_SRES-1:0] grp_q_miss;
  logic [NUM_SRES-1:0] grp_q_all_recv;
  logic [NUM_SRES-1:0] grp_q_red_done;
  logic [NUM_SRES-1:0] grp_q_soft_done;
  logic [NUM_SRES-1:0][SRE_ID_W-1:0] grp_q_master;
  logic [NUM_SRES-1:0][MAX_SRES-1:0] grp_q_mask;
  logic [NUM_SRES-1:0][2:0] grp_q_phase;

  logic [NUM_SRES-1:0] grp_tile_pulse, grp_red_pulse, grp_soft_pulse, grp_close_pulse;
  logic [NUM_SRES-1:0][GROUP_ID_W-1:0] grp_tile_gid, grp_red_gid, grp_soft_gid, grp_close_gid;

  logic grp_tile_valid, grp_red_valid, grp_soft_valid, grp_close_valid;
  logic [GROUP_ID_W-1:0] grp_tile_upd_gid, grp_red_upd_gid, grp_soft_upd_gid, grp_close_upd_gid;

  logic [NUM_SRES-1:0] hb_sre_valid, hb_sre_ready;
  logic [NUM_SRES-1:0][DATA_W-1:0] hb_sre_data;
  logic [NUM_SRES-1:0] hb_sre_last;

  logic hb_dma_valid, hb_dma_ready;

  logic [NUM_SRES-1:0] noc_tx_valid, noc_tx_ready;
  logic [NUM_SRES-1:0][NOC_PKT_W-1:0] noc_tx_pkt;
  logic [NUM_SRES-1:0] noc_rx_valid, noc_rx_ready;
  logic [NUM_SRES-1:0][NOC_PKT_W-1:0] noc_rx_pkt;

  logic dma_wr_valid, dma_wr_ready;
  logic [SRE_ID_W-1:0] dma_wr_sre_id;
  logic [DMA_ADDR_W-1:0] dma_wr_addr;
  logic [DATA_W-1:0] dma_wr_data;
  logic dma_wr_last;
  logic [TASK_ID_W-1:0] dma_wr_task_id;

  logic [NUM_SRES-1:0] sre_dma_wr_valid, sre_dma_wr_ready;

  logic dma_done_valid, dma_done_ready;
  completion_event_t dma_done_event;

  logic [NUM_SRES-1:0] sre_done_valid, sre_done_ready;
  completion_event_t sre_done_event [0:NUM_SRES-1];

  logic comp_in_valid, comp_in_ready, comp_out_valid;
  logic [$bits(completion_event_t)-1:0] comp_in_bits, comp_out_bits;
  completion_event_t comp_in_event, comp_out_event;

  integer i;

  tripim_cmd_frontend #(
    .NUM_SRES(NUM_SRES)
  ) u_cmd_fe (
    .clk                        (clk),
    .rst_n                      (rst_n),
    .cmd_valid                  (cmd_valid),
    .cmd_ready                  (cmd_ready),
    .cmd_desc                   (cmd_desc),
    .sre_task_valid_o           (sre_task_valid),
    .sre_task_ready_i           (sre_task_ready),
    .sre_task_desc_o            (sre_task_desc),
    .dma_task_valid_o           (dma_task_valid),
    .dma_task_ready_i           (dma_task_ready),
    .dma_task_desc_o            (dma_task_desc),
    .grp_create_valid_o         (grp_create_valid),
    .grp_create_ready_i         (grp_create_ready),
    .grp_create_group_id_o      (grp_create_gid),
    .grp_create_participant_mask_o(grp_create_mask),
    .grp_create_master_sre_id_o (grp_create_master),
    .grp_create_expected_tiles_o(grp_create_expected),
    .grp_create_softmax_mode_o  (grp_create_softmax_mode),
    .fe_done_valid_o            (fe_done_valid),
    .fe_done_ready_i            (fe_done_ready),
    .fe_done_event_o            (fe_done_event)
  );

  tripim_dma_engine #(
    .DATA_W(DATA_W),
    .CMD_FIFO_DEPTH(DMA_CMD_FIFO_DEPTH),
    .DMA_ADDR_W(DMA_ADDR_W)
  ) u_dma (
    .clk          (clk),
    .rst_n        (rst_n),
    .task_in_valid(dma_task_valid),
    .task_in_ready(dma_task_ready),
    .task_in_desc (dma_task_desc),
    .hb_in_valid  (hb_dma_valid),
    .hb_in_ready  (hb_dma_ready),
    .hb_in_data   (hb_in_data),
    .hb_in_last   (hb_in_last),
    .hb_task_id   (hb_task_id),
    .hb_group_id  (hb_group_id),
    .hb_opcode    (hb_opcode),
    .hb_dst_sre_id(hb_dst_sre_id),
    .dma_wr_valid (dma_wr_valid),
    .dma_wr_ready (dma_wr_ready),
    .dma_wr_sre_id(dma_wr_sre_id),
    .dma_wr_addr  (dma_wr_addr),
    .dma_wr_data  (dma_wr_data),
    .dma_wr_last  (dma_wr_last),
    .dma_wr_task_id(dma_wr_task_id),
    .done_valid   (dma_done_valid),
    .done_ready   (dma_done_ready),
    .done_event   (dma_done_event)
  );

  // HB ingress routing: DMA stream is isolated from SRE compute stream.
  always_comb begin
    hb_sre_valid = '0;
    hb_sre_data  = '0;
    hb_sre_last  = '0;
    hb_dma_valid = 1'b0;
    hb_in_ready  = 1'b0;

    if (hb_opcode == OP_DMA_H2D) begin
      hb_dma_valid = hb_in_valid;
      hb_in_ready  = hb_dma_ready;
    end else begin
      for (i = 0; i < NUM_SRES; i++) begin
        if (hb_dst_sre_id == SRE_ID_W'(i)) begin
          hb_sre_valid[i] = hb_in_valid;
          hb_sre_data[i]  = hb_in_data;
          hb_sre_last[i]  = hb_in_last;
          hb_in_ready     = hb_sre_ready[i];
        end
      end
    end
  end

  // DMA writeback to target SRE local SRAM port.
  always_comb begin
    sre_dma_wr_valid = '0;
    dma_wr_ready     = 1'b0;
    for (i = 0; i < NUM_SRES; i++) begin
      if (dma_wr_sre_id == SRE_ID_W'(i)) begin
        sre_dma_wr_valid[i] = dma_wr_valid;
        dma_wr_ready        = sre_dma_wr_ready[i];
      end
    end
  end

  tripim_noc_fabric #(
    .NUM_SRES(NUM_SRES)
  ) u_noc (
    .clk      (clk),
    .rst_n    (rst_n),
    .tx_valid_i(noc_tx_valid),
    .tx_ready_o(noc_tx_ready),
    .tx_pkt_i (noc_tx_pkt),
    .rx_valid_o(noc_rx_valid),
    .rx_ready_i(noc_rx_ready),
    .rx_pkt_o (noc_rx_pkt)
  );

  always_comb begin
    grp_tile_valid   = 1'b0;
    grp_tile_upd_gid = '0;
    grp_red_valid    = 1'b0;
    grp_red_upd_gid  = '0;
    grp_soft_valid   = 1'b0;
    grp_soft_upd_gid = '0;
    grp_close_valid  = 1'b0;
    grp_close_upd_gid= '0;

    for (i = 0; i < NUM_SRES; i++) begin
      if (!grp_tile_valid && grp_tile_pulse[i]) begin
        grp_tile_valid   = 1'b1;
        grp_tile_upd_gid = grp_tile_gid[i];
      end
      if (!grp_red_valid && grp_red_pulse[i]) begin
        grp_red_valid   = 1'b1;
        grp_red_upd_gid = grp_red_gid[i];
      end
      if (!grp_soft_valid && grp_soft_pulse[i]) begin
        grp_soft_valid   = 1'b1;
        grp_soft_upd_gid = grp_soft_gid[i];
      end
      if (!grp_close_valid && grp_close_pulse[i]) begin
        grp_close_valid   = 1'b1;
        grp_close_upd_gid = grp_close_gid[i];
      end
    end
  end

  tripim_softmax_group_table #(
    .NUM_GROUPS(NUM_GROUPS),
    .NUM_QUERY_PORTS(NUM_SRES)
  ) u_group_tbl (
    .clk                     (clk),
    .rst_n                   (rst_n),
    .create_valid            (grp_create_valid),
    .create_ready            (grp_create_ready),
    .create_group_id         (grp_create_gid),
    .create_participant_mask (grp_create_mask),
    .create_master_sre_id    (grp_create_master),
    .create_expected_tiles   (grp_create_expected),
    .create_softmax_mode     (grp_create_softmax_mode),
    .tile_recv_valid         (grp_tile_valid),
    .tile_recv_group_id      (grp_tile_upd_gid),
    .reduction_done_valid    (grp_red_valid),
    .reduction_done_group_id (grp_red_upd_gid),
    .softmax_done_valid      (grp_soft_valid),
    .softmax_done_group_id   (grp_soft_upd_gid),
    .group_close_valid       (grp_close_valid),
    .group_close_group_id    (grp_close_upd_gid),
    .query_valid_i           (grp_q_valid),
    .query_group_id_i        (grp_q_gid),
    .query_hit_o             (grp_q_hit),
    .query_miss_o            (grp_q_miss),
    .query_all_tiles_received_o(grp_q_all_recv),
    .query_reduction_done_o  (grp_q_red_done),
    .query_softmax_done_o    (grp_q_soft_done),
    .query_master_sre_o      (grp_q_master),
    .query_participant_mask_o(grp_q_mask),
    .query_phase_o           (grp_q_phase),
    .group_valid_o           (),
    .reduction_done_o        (),
    .softmax_done_o          ()
  );

  genvar g;
  generate
    for (g = 0; g < NUM_SRES; g++) begin : GEN_SRE
      localparam int CH_ID = 0;
      localparam int BK_ID = g;

      tripim_sre #(
        .SRE_ID(g),
        .CHANNEL_ID(CH_ID),
        .BANK_ID(BK_ID),
        .DATA_W(DATA_W),
        .TASK_QUEUE_DEPTH(TASK_QUEUE_DEPTH),
        .BUFFER_BYTES(BUFFER_BYTES),
        .DMA_ADDR_W(DMA_ADDR_W)
      ) u_sre (
        .clk                     (clk),
        .rst_n                   (rst_n),
        .task_in_valid           (sre_task_valid[g]),
        .task_in_ready           (sre_task_ready[g]),
        .task_in_desc            (sre_task_desc[g]),
        .hb_in_valid             (hb_sre_valid[g]),
        .hb_in_ready             (hb_sre_ready[g]),
        .hb_in_data              (hb_sre_data[g]),
        .hb_in_last              (hb_sre_last[g]),
        .hb_task_id              (hb_task_id),
        .hb_group_id             (hb_group_id),
        .hb_opcode               (hb_opcode),
        .hb_dst_sre_id           (hb_dst_sre_id),
        .noc_rx_valid            (noc_rx_valid[g]),
        .noc_rx_ready            (noc_rx_ready[g]),
        .noc_rx_pkt              (noc_rx_pkt[g]),
        .noc_tx_valid            (noc_tx_valid[g]),
        .noc_tx_ready            (noc_tx_ready[g]),
        .noc_tx_pkt              (noc_tx_pkt[g]),
        .grp_query_valid_o       (grp_q_valid[g]),
        .grp_query_group_id_o    (grp_q_gid[g]),
        .grp_query_hit_i         (grp_q_hit[g]),
        .grp_query_miss_i        (grp_q_miss[g]),
        .grp_query_all_tiles_received_i(grp_q_all_recv[g]),
        .grp_query_reduction_done_i(grp_q_red_done[g]),
        .grp_query_softmax_done_i(grp_q_soft_done[g]),
        .grp_query_master_sre_i  (grp_q_master[g]),
        .grp_query_participant_mask_i(grp_q_mask[g]),
        .grp_query_phase_i       (grp_q_phase[g]),
        .grp_tile_recv_pulse     (grp_tile_pulse[g]),
        .grp_tile_recv_gid       (grp_tile_gid[g]),
        .grp_reduction_done_pulse(grp_red_pulse[g]),
        .grp_reduction_done_gid  (grp_red_gid[g]),
        .grp_softmax_done_pulse  (grp_soft_pulse[g]),
        .grp_softmax_done_gid    (grp_soft_gid[g]),
        .grp_close_pulse         (grp_close_pulse[g]),
        .grp_close_gid           (grp_close_gid[g]),
        .dma_wr_valid            (sre_dma_wr_valid[g]),
        .dma_wr_ready            (sre_dma_wr_ready[g]),
        .dma_wr_addr             (dma_wr_addr),
        .dma_wr_data             (dma_wr_data),
        .dma_wr_last             (dma_wr_last),
        .dma_wr_task_id          (dma_wr_task_id),
        .done_valid              (sre_done_valid[g]),
        .done_ready              (sre_done_ready[g]),
        .done_event              (sre_done_event[g])
      );
    end
  endgenerate

  // Completion arbitration: frontend errors > DMA > SRE array.
  logic sre_sel_valid;
  logic [$clog2(NUM_SRES)-1:0] sre_sel_idx;

  always_comb begin
    sre_sel_valid = 1'b0;
    sre_sel_idx   = '0;
    for (i = 0; i < NUM_SRES; i++) begin
      if (!sre_sel_valid && sre_done_valid[i]) begin
        sre_sel_valid = 1'b1;
        sre_sel_idx   = i[$clog2(NUM_SRES)-1:0];
      end
    end

    comp_in_valid = 1'b0;
    comp_in_event = '0;

    fe_done_ready  = 1'b0;
    dma_done_ready = 1'b0;
    sre_done_ready = '0;

    if (fe_done_valid) begin
      comp_in_valid = 1'b1;
      comp_in_event = fe_done_event;
      fe_done_ready = comp_in_ready;
    end else if (dma_done_valid) begin
      comp_in_valid  = 1'b1;
      comp_in_event  = dma_done_event;
      dma_done_ready = comp_in_ready;
    end else if (sre_sel_valid) begin
      comp_in_valid = 1'b1;
      comp_in_event = sre_done_event[sre_sel_idx];
      sre_done_ready[sre_sel_idx] = comp_in_ready;
    end
  end

  assign comp_in_bits  = comp_in_event;
  assign comp_out_event = completion_event_t'(comp_out_bits);

  tripim_completion_fifo #(
    .WIDTH($bits(completion_event_t)),
    .DEPTH(COMPLETION_QUEUE_DEPTH)
  ) u_doneq (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (comp_in_valid),
    .in_ready (comp_in_ready),
    .in_data  (comp_in_bits),
    .out_valid(comp_out_valid),
    .out_ready(done_ready),
    .out_data (comp_out_bits),
    .level    ()
  );

  assign done_valid   = comp_out_valid;
  assign done_task_id = comp_out_event.task_id;
  assign done_opcode  = comp_out_event.opcode;
  assign done_tag     = comp_out_event.tag;
  assign done_status  = comp_out_event.status;
endmodule
