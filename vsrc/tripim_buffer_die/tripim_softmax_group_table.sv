module tripim_softmax_group_table #(
  parameter int NUM_GROUPS      = 16,
  parameter int NUM_QUERY_PORTS = 8
)(
  input  logic clk,
  input  logic rst_n,

  input  logic                                   create_valid,
  output logic                                   create_ready,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]      create_group_id,
  input  logic [tripim_pkg::MAX_SRES-1:0]        create_participant_mask,
  input  logic [tripim_pkg::SRE_ID_W-1:0]        create_master_sre_id,
  input  logic [tripim_pkg::TILE_CNT_W-1:0]      create_expected_tiles,
  input  logic                                   create_softmax_mode,

  input  logic                                   tile_recv_valid,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]      tile_recv_group_id,

  input  logic                                   reduction_done_valid,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]      reduction_done_group_id,

  input  logic                                   softmax_done_valid,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]      softmax_done_group_id,

  input  logic                                   group_close_valid,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]      group_close_group_id,

  input  logic [NUM_QUERY_PORTS-1:0]                                   query_valid_i,
  input  logic [NUM_QUERY_PORTS-1:0][tripim_pkg::GROUP_ID_W-1:0]       query_group_id_i,
  output logic [NUM_QUERY_PORTS-1:0]                                   query_hit_o,
  output logic [NUM_QUERY_PORTS-1:0]                                   query_miss_o,
  output logic [NUM_QUERY_PORTS-1:0]                                   query_all_tiles_received_o,
  output logic [NUM_QUERY_PORTS-1:0]                                   query_reduction_done_o,
  output logic [NUM_QUERY_PORTS-1:0]                                   query_softmax_done_o,
  output logic [NUM_QUERY_PORTS-1:0][tripim_pkg::SRE_ID_W-1:0]         query_master_sre_o,
  output logic [NUM_QUERY_PORTS-1:0][tripim_pkg::MAX_SRES-1:0]         query_participant_mask_o,
  output logic [NUM_QUERY_PORTS-1:0][2:0]                              query_phase_o,

  output logic [NUM_GROUPS-1:0]                                        group_valid_o,
  output logic [NUM_GROUPS-1:0]                                        reduction_done_o,
  output logic [NUM_GROUPS-1:0]                                        softmax_done_o
);
  import tripim_pkg::*;

  logic [NUM_GROUPS-1:0] valid_q;
  logic [NUM_GROUPS-1:0] reduction_done_q;
  logic [NUM_GROUPS-1:0] softmax_done_q;
  logic [NUM_GROUPS-1:0] softmax_mode_q;

  logic [MAX_SRES-1:0] participant_mask_q [0:NUM_GROUPS-1];
  logic [SRE_ID_W-1:0] master_sre_q [0:NUM_GROUPS-1];
  logic [TILE_CNT_W-1:0] expected_tiles_q [0:NUM_GROUPS-1];
  logic [TILE_CNT_W-1:0] received_tiles_q [0:NUM_GROUPS-1];
  group_phase_e phase_q [0:NUM_GROUPS-1];

  assign create_ready     = !valid_q[create_group_id];
  assign group_valid_o    = valid_q;
  assign reduction_done_o = reduction_done_q;
  assign softmax_done_o   = softmax_done_q;

  integer i;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid_q          <= '0;
      reduction_done_q <= '0;
      softmax_done_q   <= '0;
      softmax_mode_q   <= '0;
      for (i = 0; i < NUM_GROUPS; i++) begin
        participant_mask_q[i] <= '0;
        master_sre_q[i]       <= '0;
        expected_tiles_q[i]   <= '0;
        received_tiles_q[i]   <= '0;
        phase_q[i]            <= GRP_PHASE_INVALID;
      end
    end else begin
      if (create_valid && create_ready) begin
        valid_q[create_group_id]            <= 1'b1;
        participant_mask_q[create_group_id] <= create_participant_mask;
        master_sre_q[create_group_id]       <= create_master_sre_id;
        expected_tiles_q[create_group_id]   <= create_expected_tiles;
        received_tiles_q[create_group_id]   <= '0;
        reduction_done_q[create_group_id]   <= 1'b0;
        softmax_done_q[create_group_id]     <= 1'b0;
        softmax_mode_q[create_group_id]     <= create_softmax_mode;
        phase_q[create_group_id]            <= GRP_PHASE_COLLECT;
      end

      if (tile_recv_valid && valid_q[tile_recv_group_id]) begin
        if (received_tiles_q[tile_recv_group_id] < expected_tiles_q[tile_recv_group_id]) begin
          received_tiles_q[tile_recv_group_id] <= received_tiles_q[tile_recv_group_id] + 1'b1;
        end
      end

      if (reduction_done_valid && valid_q[reduction_done_group_id]) begin
        reduction_done_q[reduction_done_group_id] <= 1'b1;
        if (softmax_mode_q[reduction_done_group_id])
          phase_q[reduction_done_group_id] <= GRP_PHASE_SOFTMAX;
        else
          phase_q[reduction_done_group_id] <= GRP_PHASE_DONE;
      end

      if (softmax_done_valid && valid_q[softmax_done_group_id]) begin
        softmax_done_q[softmax_done_group_id] <= 1'b1;
        phase_q[softmax_done_group_id]        <= GRP_PHASE_DONE;
      end

      if (group_close_valid && valid_q[group_close_group_id]) begin
        valid_q[group_close_group_id] <= 1'b0;
      end
    end
  end

  integer q;
  always_comb begin
    query_hit_o                = '0;
    query_miss_o               = '0;
    query_all_tiles_received_o = '0;
    query_reduction_done_o     = '0;
    query_softmax_done_o       = '0;
    query_master_sre_o         = '0;
    query_participant_mask_o   = '0;
    query_phase_o              = '0;

    for (q = 0; q < NUM_QUERY_PORTS; q++) begin
      if (query_valid_i[q]) begin
        query_hit_o[q]  = valid_q[query_group_id_i[q]];
        query_miss_o[q] = !valid_q[query_group_id_i[q]];
        if (valid_q[query_group_id_i[q]]) begin
          query_master_sre_o[q]       = master_sre_q[query_group_id_i[q]];
          query_participant_mask_o[q] = participant_mask_q[query_group_id_i[q]];
          query_reduction_done_o[q]   = reduction_done_q[query_group_id_i[q]];
          query_softmax_done_o[q]     = softmax_done_q[query_group_id_i[q]];
          query_phase_o[q]            = phase_q[query_group_id_i[q]];
          query_all_tiles_received_o[q] =
            (received_tiles_q[query_group_id_i[q]] >= expected_tiles_q[query_group_id_i[q]]) &&
            (expected_tiles_q[query_group_id_i[q]] != '0);
        end else begin
          query_phase_o[q] = GRP_PHASE_INVALID;
        end
      end
    end
  end
endmodule
