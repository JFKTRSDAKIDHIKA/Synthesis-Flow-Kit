module tripim_cmd_frontend #(
  parameter int NUM_SRES          = 8
)(
  input  logic clk,
  input  logic rst_n,

  input  logic                   cmd_valid,
  output logic                   cmd_ready,
  input  tripim_pkg::task_desc_t cmd_desc,

  output logic [NUM_SRES-1:0]                    sre_task_valid_o,
  input  logic [NUM_SRES-1:0]                    sre_task_ready_i,
  output tripim_pkg::task_desc_t                 sre_task_desc_o [0:NUM_SRES-1],

  output logic                                   dma_task_valid_o,
  input  logic                                   dma_task_ready_i,
  output tripim_pkg::task_desc_t                 dma_task_desc_o,

  output logic                                   grp_create_valid_o,
  input  logic                                   grp_create_ready_i,
  output logic [tripim_pkg::GROUP_ID_W-1:0]      grp_create_group_id_o,
  output logic [tripim_pkg::MAX_SRES-1:0]        grp_create_participant_mask_o,
  output logic [tripim_pkg::SRE_ID_W-1:0]        grp_create_master_sre_id_o,
  output logic [tripim_pkg::TILE_CNT_W-1:0]      grp_create_expected_tiles_o,
  output logic                                   grp_create_softmax_mode_o,

  output logic                                   fe_done_valid_o,
  input  logic                                   fe_done_ready_i,
  output tripim_pkg::completion_event_t          fe_done_event_o
);
  import tripim_pkg::*;

  logic legal_cmd;
  status_e illegal_status;
  logic soft_grouped_op;
  logic is_master_task;
  logic map_ok_single_channel;
  logic [MAX_SRES-1:0] master_bitmask;
  logic [TILE_CNT_W-1:0] participant_cnt;

  integer i;
  always_comb begin
    for (i = 0; i < NUM_SRES; i++) sre_task_desc_o[i] = cmd_desc;
    dma_task_desc_o = cmd_desc;

    soft_grouped_op = (cmd_desc.opcode == OP_SOFTMAX) || (cmd_desc.opcode == OP_REDUCTION);
    is_master_task  = (cmd_desc.src_sre_id == cmd_desc.dst_sre_id);
    master_bitmask  = ({{(MAX_SRES-1){1'b0}}, 1'b1} << cmd_desc.src_sre_id);
    participant_cnt = '0;
    for (i = 0; i < NUM_SRES; i++) begin
      if ((cmd_desc.participant_mask & ({{(MAX_SRES-1){1'b0}}, 1'b1} << i)) != '0) begin
        participant_cnt = participant_cnt + 1'b1;
      end
    end

    map_ok_single_channel = (cmd_desc.channel_id == '0) &&
                            (cmd_desc.bank_id < NUM_SRES) &&
                            (cmd_desc.dst_sre_id == cmd_desc.bank_id);

    legal_cmd = 1'b1;
    illegal_status = STATUS_OK;

    if (!cmd_desc.valid) begin
      legal_cmd = 1'b0;
      illegal_status = STATUS_BAD_OPCODE;
    end else if (!opcode_supported(cmd_desc.opcode)) begin
      legal_cmd = 1'b0;
      illegal_status = STATUS_BAD_OPCODE;
    end else if (cmd_desc.dst_sre_id >= NUM_SRES) begin
      legal_cmd = 1'b0;
      illegal_status = STATUS_BAD_DST;
    end else if (!map_ok_single_channel && (cmd_desc.opcode != OP_DMA_H2D)) begin
      legal_cmd = 1'b0;
      illegal_status = STATUS_BAD_DST;
    end else if (soft_grouped_op) begin
      if ((cmd_desc.participant_mask == '0) ||
          (cmd_desc.expected_tiles == '0) ||
          (cmd_desc.src_sre_id >= NUM_SRES) ||
          ((cmd_desc.participant_mask & master_bitmask) == '0) ||
          (cmd_desc.expected_tiles != participant_cnt)) begin
        legal_cmd = 1'b0;
        illegal_status = STATUS_BAD_GROUP;
      end
    end
  end

  always_comb begin
    sre_task_valid_o = '0;
    dma_task_valid_o = 1'b0;

    grp_create_valid_o            = 1'b0;
    grp_create_group_id_o         = cmd_desc.group_id;
    grp_create_participant_mask_o = cmd_desc.participant_mask;
    grp_create_master_sre_id_o    = cmd_desc.src_sre_id;
    grp_create_expected_tiles_o   = cmd_desc.expected_tiles;
    grp_create_softmax_mode_o     = (cmd_desc.opcode == OP_SOFTMAX);

    fe_done_valid_o = 1'b0;
    fe_done_event_o.task_id = cmd_desc.task_id;
    fe_done_event_o.opcode  = cmd_desc.opcode;
    fe_done_event_o.tag     = cmd_desc.tag;
    fe_done_event_o.status  = illegal_status;

    cmd_ready = 1'b0;

    if (cmd_valid) begin
      if (!legal_cmd) begin
        fe_done_valid_o = 1'b1;
        cmd_ready       = fe_done_ready_i;
      end else if (cmd_desc.opcode == OP_DMA_H2D) begin
        dma_task_valid_o = 1'b1;
        cmd_ready        = dma_task_ready_i;
      end else begin
        sre_task_valid_o[cmd_desc.dst_sre_id] = 1'b1;
        cmd_ready = sre_task_ready_i[cmd_desc.dst_sre_id];

        if (soft_grouped_op && is_master_task) begin
          grp_create_valid_o = 1'b1;
          cmd_ready = cmd_ready & grp_create_ready_i;
        end
      end
    end
  end
endmodule
