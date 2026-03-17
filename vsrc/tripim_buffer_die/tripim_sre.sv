module tripim_sre #(
  parameter int SRE_ID            = 0,
  parameter int CHANNEL_ID        = 0,
  parameter int BANK_ID           = 0,
  parameter int DATA_W            = 64,
  parameter int TASK_QUEUE_DEPTH  = 8,
  parameter int ING_FIFO_DEPTH    = 8,
  parameter int EGR_FIFO_DEPTH    = 8,
  parameter int BUFFER_BYTES      = 4096,
  parameter int DMA_ADDR_W        = 12
)(
  input  logic clk,
  input  logic rst_n,

  input  logic                     task_in_valid,
  output logic                     task_in_ready,
  input  tripim_pkg::task_desc_t   task_in_desc,

  input  logic                                hb_in_valid,
  output logic                                hb_in_ready,
  input  logic [DATA_W-1:0]                   hb_in_data,
  input  logic                                hb_in_last,
  input  logic [tripim_pkg::TASK_ID_W-1:0]    hb_task_id,
  input  logic [tripim_pkg::GROUP_ID_W-1:0]   hb_group_id,
  input  tripim_pkg::opcode_e                 hb_opcode,
  input  logic [tripim_pkg::SRE_ID_W-1:0]     hb_dst_sre_id,

  input  logic                                noc_rx_valid,
  output logic                                noc_rx_ready,
  input  logic [$bits(tripim_pkg::noc_pkt_t)-1:0] noc_rx_pkt,

  output logic                                noc_tx_valid,
  input  logic                                noc_tx_ready,
  output logic [$bits(tripim_pkg::noc_pkt_t)-1:0] noc_tx_pkt,

  output logic                                grp_query_valid_o,
  output logic [tripim_pkg::GROUP_ID_W-1:0]   grp_query_group_id_o,
  input  logic                                grp_query_hit_i,
  input  logic                                grp_query_miss_i,
  input  logic                                grp_query_all_tiles_received_i,
  input  logic                                grp_query_reduction_done_i,
  input  logic                                grp_query_softmax_done_i,
  input  logic [tripim_pkg::SRE_ID_W-1:0]     grp_query_master_sre_i,
  input  logic [tripim_pkg::MAX_SRES-1:0]     grp_query_participant_mask_i,
  input  logic [2:0]                          grp_query_phase_i,

  output logic                                grp_tile_recv_pulse,
  output logic [tripim_pkg::GROUP_ID_W-1:0]   grp_tile_recv_gid,
  output logic                                grp_reduction_done_pulse,
  output logic [tripim_pkg::GROUP_ID_W-1:0]   grp_reduction_done_gid,
  output logic                                grp_softmax_done_pulse,
  output logic [tripim_pkg::GROUP_ID_W-1:0]   grp_softmax_done_gid,
  output logic                                grp_close_pulse,
  output logic [tripim_pkg::GROUP_ID_W-1:0]   grp_close_gid,

  input  logic                                dma_wr_valid,
  output logic                                dma_wr_ready,
  input  logic [DMA_ADDR_W-1:0]               dma_wr_addr,
  input  logic [DATA_W-1:0]                   dma_wr_data,
  input  logic                                dma_wr_last,
  input  logic [tripim_pkg::TASK_ID_W-1:0]    dma_wr_task_id,

  output logic                           done_valid,
  input  logic                           done_ready,
  output tripim_pkg::completion_event_t done_event
);
  import tripim_pkg::*;

  localparam int NOC_PKT_W     = $bits(noc_pkt_t);
  localparam int BEAT_BYTES    = DATA_W/8;
  localparam int BUFFER_BEATS  = (BUFFER_BYTES/BEAT_BYTES);
  localparam int ADDR_W        = (BUFFER_BEATS <= 2) ? 1 : $clog2(BUFFER_BEATS);

  typedef enum logic [3:0] {
    ST_IDLE,
    ST_FETCH_TASK,
    ST_RESERVE_BUFFER,
    ST_RECV_HB_TILE,
    ST_SEND_NOC,
    ST_WAIT_GROUP_READY,
    ST_REDUCE,
    ST_SOFTMAX,
    ST_BCAST_RESULT,
    ST_COMPLETE
  } sre_state_e;

  sre_state_e state_q;
  task_desc_t active_task_q;

  // Single-port local SRAM model.
  logic [DATA_W-1:0] local_sram [0:BUFFER_BEATS-1];

  logic [ADDR_W-1:0] alloc_ptr_q;
  logic [ADDR_W-1:0] local_base_ptr_q;
  logic [ADDR_W-1:0] local_wr_ptr_q;
  logic [ADDR_W-1:0] local_rd_ptr_q;
  logic [ADDR_W-1:0] gather_wr_ptr_q;

  logic [TILE_BYTES_W-1:0] buf_used_bytes_q;
  logic [TILE_BYTES_W-1:0] reserved_bytes_q;
  logic [TILE_CNT_W-1:0]   tile_beats_q;
  logic [TILE_BYTES_W-1:0] hb_bytes_q;
  logic [TILE_CNT_W-1:0]   send_beats_q;
  logic [TILE_CNT_W-1:0]   gathered_tiles_q;

  logic [15:0] reduce_ctr_q;
  logic [15:0] softmax_ctr_q;
  logic [31:0] result_acc_q;

  logic reserved_valid_q;
  logic bad_hb_meta_q;
  status_e status_q;

  logic completion_pending_q;
  logic completion_sent_q;

  logic taskq_out_valid, taskq_out_ready;
  logic [$bits(task_desc_t)-1:0] taskq_out_bits;
  task_desc_t taskq_head;

  logic ing_in_ready, ing_out_valid, ing_out_ready;
  logic [NOC_PKT_W-1:0] ing_out_bits;
  noc_pkt_t ing_pkt;

  logic egr_in_valid, egr_in_ready, egr_out_valid, egr_out_ready;
  logic [NOC_PKT_W-1:0] egr_in_bits, egr_out_bits;
  noc_pkt_t egr_pkt_c;

  logic grouped_op;
  logic is_master;
  logic hb_meta_ok;
  logic hb_fire;
  logic ing_fire;
  logic [MAX_SRES-1:0] self_mask;

  // Local SRAM arbitration requests.
  logic wr_req_local;
  logic wr_req_hb;
  logic wr_req_gather;
  logic wr_req_dma;
  logic rd_req_local;
  logic rd_grant_local;
  logic [ADDR_W-1:0] wr_addr_local;
  logic [DATA_W-1:0] wr_data_local;

  logic local_store_pending_q;
  logic [ADDR_W-1:0] local_store_addr_q;
  logic [DATA_W-1:0] local_store_data_q;

  logic gather_pkt_match;
  logic bcast_final_match;
  logic bcast_result_match;

  logic bcast_phase_q;

  assign taskq_head = task_desc_t'(taskq_out_bits);
  assign ing_pkt    = noc_pkt_t'(ing_out_bits);

  assign grouped_op = (active_task_q.opcode == OP_SOFTMAX) || (active_task_q.opcode == OP_REDUCTION);
  assign is_master  = (active_task_q.src_sre_id == SRE_ID_W'(SRE_ID));
  assign self_mask  = ({{(MAX_SRES-1){1'b0}},1'b1} << SRE_ID);

  assign hb_meta_ok = (hb_dst_sre_id == SRE_ID_W'(SRE_ID)) &&
                      (hb_task_id   == active_task_q.task_id) &&
                      (hb_opcode    == active_task_q.opcode) &&
                      ((!grouped_op) || (hb_group_id == active_task_q.group_id));

  assign hb_fire  = hb_in_valid && hb_in_ready;
  assign ing_fire = ing_out_valid && ing_out_ready;

  assign grp_query_valid_o    = (state_q == ST_WAIT_GROUP_READY) && grouped_op && is_master;
  assign grp_query_group_id_o = active_task_q.group_id;

  tripim_task_fifo #(
    .WIDTH($bits(task_desc_t)),
    .DEPTH(TASK_QUEUE_DEPTH)
  ) u_taskq (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (task_in_valid),
    .in_ready (task_in_ready),
    .in_data  (task_in_desc),
    .out_valid(taskq_out_valid),
    .out_ready(taskq_out_ready),
    .out_data (taskq_out_bits),
    .level    ()
  );

  tripim_task_fifo #(
    .WIDTH(NOC_PKT_W),
    .DEPTH(ING_FIFO_DEPTH)
  ) u_noc_ing (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (noc_rx_valid),
    .in_ready (ing_in_ready),
    .in_data  (noc_rx_pkt),
    .out_valid(ing_out_valid),
    .out_ready(ing_out_ready),
    .out_data (ing_out_bits),
    .level    ()
  );

  tripim_task_fifo #(
    .WIDTH(NOC_PKT_W),
    .DEPTH(EGR_FIFO_DEPTH)
  ) u_noc_egr (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (egr_in_valid),
    .in_ready (egr_in_ready),
    .in_data  (egr_in_bits),
    .out_valid(egr_out_valid),
    .out_ready(egr_out_ready),
    .out_data (egr_out_bits),
    .level    ()
  );

  assign noc_rx_ready = ing_in_ready;
  assign noc_tx_valid = egr_out_valid;
  assign noc_tx_pkt   = egr_out_bits;
  assign egr_out_ready = noc_tx_ready;

  // Packet checks.
  assign gather_pkt_match = (ing_pkt.pkt_type == PKT_TILE_DATA) &&
                            (ing_pkt.group_id == active_task_q.group_id) &&
                            (ing_pkt.task_id  == active_task_q.task_id) &&
                            (ing_pkt.dst_sre_id == SRE_ID_W'(SRE_ID));

  assign bcast_result_match = ing_pkt.bcast &&
                              (ing_pkt.group_id == active_task_q.group_id) &&
                              (ing_pkt.task_id  == active_task_q.task_id) &&
                              (ing_pkt.src_sre_id == active_task_q.src_sre_id) &&
                              ((ing_pkt.pkt_type == PKT_REDUCTION_RESULT) ||
                               (ing_pkt.pkt_type == PKT_SOFTMAX_RESULT));

  assign bcast_final_match = ing_pkt.bcast &&
                             (ing_pkt.pkt_type == PKT_FINAL_BCAST) &&
                             (ing_pkt.group_id == active_task_q.group_id) &&
                             (ing_pkt.task_id  == active_task_q.task_id) &&
                             (ing_pkt.src_sre_id == active_task_q.src_sre_id) &&
                             (ing_pkt.opcode == active_task_q.opcode);

  // Local SRAM arbitration.
  assign wr_req_local  = local_store_pending_q;
  assign wr_addr_local = local_store_addr_q;
  assign wr_data_local = local_store_data_q;

  assign wr_req_hb     = (state_q == ST_RECV_HB_TILE) && hb_fire && hb_meta_ok;
  assign wr_req_gather = (state_q == ST_WAIT_GROUP_READY) && is_master && ing_fire && gather_pkt_match;

  assign rd_req_local   = (state_q == ST_SEND_NOC);
  assign rd_grant_local = rd_req_local && !wr_req_local && !wr_req_hb && !wr_req_gather;

  // DMA writes are lowest priority and get backpressured by local traffic.
  assign dma_wr_ready = (dma_wr_addr < BUFFER_BEATS) && !wr_req_local && !wr_req_hb && !wr_req_gather && !rd_req_local;
  assign wr_req_dma   = dma_wr_valid && dma_wr_ready;

  always_comb begin
    hb_in_ready    = 1'b0;
    ing_out_ready  = 1'b0;
    egr_in_valid   = 1'b0;
    egr_in_bits    = '0;
    egr_pkt_c      = '0;

    done_valid = completion_pending_q && !completion_sent_q;
    done_event.task_id = active_task_q.task_id;
    done_event.opcode  = active_task_q.opcode;
    done_event.tag     = active_task_q.tag;
    done_event.status  = status_q;

    case (state_q)
      ST_RECV_HB_TILE: begin
        // HB metadata mismatch is consumed and retired with BAD_HB_METADATA.
        hb_in_ready = 1'b1;
      end

      ST_SEND_NOC: begin
        egr_in_valid = rd_grant_local;
        egr_pkt_c.pkt_type   = PKT_TILE_DATA;
        egr_pkt_c.opcode     = active_task_q.opcode;
        egr_pkt_c.task_id    = active_task_q.task_id;
        egr_pkt_c.group_id   = active_task_q.group_id;
        egr_pkt_c.src_sre_id = SRE_ID_W'(SRE_ID);
        egr_pkt_c.dst_sre_id = active_task_q.src_sre_id;
        egr_pkt_c.bcast      = 1'b0;
        egr_pkt_c.bcast_mask = '0;
        egr_pkt_c.last       = (send_beats_q + 1'b1 >= tile_beats_q);
        egr_pkt_c.payload    = local_sram[local_rd_ptr_q][63:0];
        egr_in_bits = egr_pkt_c;
      end

      ST_WAIT_GROUP_READY: begin
        ing_out_ready = ing_out_valid;
      end

      ST_BCAST_RESULT: begin
        egr_in_valid = 1'b1;
        if (!bcast_phase_q) begin
          if (active_task_q.opcode == OP_SOFTMAX) egr_pkt_c.pkt_type = PKT_SOFTMAX_RESULT;
          else                                    egr_pkt_c.pkt_type = PKT_REDUCTION_RESULT;
        end else begin
          egr_pkt_c.pkt_type = PKT_FINAL_BCAST;
        end
        egr_pkt_c.opcode     = active_task_q.opcode;
        egr_pkt_c.task_id    = active_task_q.task_id;
        egr_pkt_c.group_id   = active_task_q.group_id;
        egr_pkt_c.src_sre_id = SRE_ID_W'(SRE_ID);
        egr_pkt_c.dst_sre_id = '0;
        egr_pkt_c.bcast      = 1'b1;
        egr_pkt_c.bcast_mask = active_task_q.participant_mask & ~self_mask;
        egr_pkt_c.last       = 1'b1;
        egr_pkt_c.payload    = {32'h0, result_acc_q};
        egr_in_bits = egr_pkt_c;
      end

      default: ;
    endcase
  end

  assign taskq_out_ready = (state_q == ST_FETCH_TASK) && taskq_out_valid;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_q          <= ST_IDLE;
      active_task_q    <= '0;
      alloc_ptr_q      <= '0;
      local_base_ptr_q <= '0;
      local_wr_ptr_q   <= '0;
      local_rd_ptr_q   <= '0;
      gather_wr_ptr_q  <= '0;
      buf_used_bytes_q <= '0;
      reserved_bytes_q <= '0;
      tile_beats_q     <= '0;
      hb_bytes_q       <= '0;
      send_beats_q     <= '0;
      gathered_tiles_q <= '0;
      reduce_ctr_q     <= '0;
      softmax_ctr_q    <= '0;
      result_acc_q     <= '0;
      reserved_valid_q <= 1'b0;
      bad_hb_meta_q    <= 1'b0;
      status_q         <= STATUS_OK;
      completion_pending_q <= 1'b0;
      completion_sent_q    <= 1'b0;
      local_store_pending_q<= 1'b0;
      local_store_addr_q   <= '0;
      local_store_data_q   <= '0;
      bcast_phase_q        <= 1'b0;

      grp_tile_recv_pulse      <= 1'b0;
      grp_tile_recv_gid        <= '0;
      grp_reduction_done_pulse <= 1'b0;
      grp_reduction_done_gid   <= '0;
      grp_softmax_done_pulse   <= 1'b0;
      grp_softmax_done_gid     <= '0;
      grp_close_pulse          <= 1'b0;
      grp_close_gid            <= '0;
    end else begin
      grp_tile_recv_pulse      <= 1'b0;
      grp_reduction_done_pulse <= 1'b0;
      grp_softmax_done_pulse   <= 1'b0;
      grp_close_pulse          <= 1'b0;

      // Single-port SRAM write arbitration: LOCAL > HB > GATHER > DMA.
      if (wr_req_local) begin
        local_sram[wr_addr_local] <= wr_data_local;
        local_store_pending_q <= 1'b0;
      end else if (wr_req_hb) begin
        local_sram[local_wr_ptr_q] <= hb_in_data;
      end else if (wr_req_gather) begin
        local_sram[gather_wr_ptr_q] <= {{(DATA_W-64){1'b0}}, ing_pkt.payload};
      end else if (wr_req_dma) begin
        local_sram[dma_wr_addr] <= dma_wr_data;
      end

      case (state_q)
        ST_IDLE: begin
          if (taskq_out_valid) state_q <= ST_FETCH_TASK;
        end

        ST_FETCH_TASK: begin
          if (taskq_out_valid) begin
            active_task_q    <= taskq_head;
            hb_bytes_q       <= '0;
            send_beats_q     <= '0;
            gathered_tiles_q <= '0;
            reduce_ctr_q     <= '0;
            softmax_ctr_q    <= '0;
            result_acc_q     <= '0;
            bad_hb_meta_q    <= 1'b0;
            status_q         <= STATUS_OK;
            reserved_valid_q <= 1'b0;
            completion_pending_q <= 1'b0;
            completion_sent_q    <= 1'b0;
            bcast_phase_q        <= 1'b0;

            if ((taskq_head.opcode == OP_DMA_H2D) || !opcode_supported(taskq_head.opcode)) begin
              status_q <= STATUS_BAD_OPCODE;
              completion_pending_q <= 1'b1;
              state_q  <= ST_COMPLETE;
            end else begin
              state_q <= ST_RESERVE_BUFFER;
            end
          end
        end

        ST_RESERVE_BUFFER: begin
          if (buf_used_bytes_q + active_task_q.tile_bytes > BUFFER_BYTES) begin
            status_q <= STATUS_BUFFER_OVERFLOW;
            completion_pending_q <= 1'b1;
            state_q  <= ST_COMPLETE;
          end else begin
            tile_beats_q     <= (active_task_q.tile_bytes + BEAT_BYTES - 1) / BEAT_BYTES;
            reserved_bytes_q <= active_task_q.tile_bytes;
            reserved_valid_q <= 1'b1;
            buf_used_bytes_q <= buf_used_bytes_q + active_task_q.tile_bytes;

            local_base_ptr_q <= alloc_ptr_q;
            local_wr_ptr_q   <= alloc_ptr_q;
            local_rd_ptr_q   <= alloc_ptr_q;
            gather_wr_ptr_q  <= alloc_ptr_q + ((active_task_q.tile_bytes + BEAT_BYTES - 1) / BEAT_BYTES);
            alloc_ptr_q      <= alloc_ptr_q + (2 * ((active_task_q.tile_bytes + BEAT_BYTES - 1) / BEAT_BYTES));

            hb_bytes_q       <= '0;
            send_beats_q     <= '0;
            gathered_tiles_q <= '0;
            state_q          <= ST_RECV_HB_TILE;
          end
        end

        ST_RECV_HB_TILE: begin
          if (hb_fire) begin
            hb_bytes_q <= hb_bytes_q + BEAT_BYTES;

            if (!hb_meta_ok) begin
              bad_hb_meta_q <= 1'b1;
              status_q      <= STATUS_BAD_HB_METADATA;
            end else begin
              local_wr_ptr_q <= local_wr_ptr_q + 1'b1;
              result_acc_q   <= result_acc_q + hb_in_data[31:0];
            end

            if (hb_in_last || (hb_bytes_q + BEAT_BYTES >= active_task_q.tile_bytes)) begin
              if (grouped_op && !bad_hb_meta_q && hb_meta_ok) begin
                grp_tile_recv_pulse <= 1'b1;
                grp_tile_recv_gid   <= active_task_q.group_id;
              end

              if (!hb_meta_ok || bad_hb_meta_q) begin
                completion_pending_q <= 1'b1;
                state_q <= ST_COMPLETE;
              end else if (active_task_q.opcode == OP_TILE_MOVE) begin
                completion_pending_q <= 1'b1;
                state_q <= ST_COMPLETE;
              end else if (active_task_q.opcode == OP_REDUCTION) begin
                if (is_master) begin
                  gathered_tiles_q <= 1;
                  if (active_task_q.expected_tiles <= 1) state_q <= ST_REDUCE;
                  else                                   state_q <= ST_WAIT_GROUP_READY;
                end else begin
                  local_rd_ptr_q <= local_base_ptr_q;
                  send_beats_q   <= '0;
                  state_q        <= ST_SEND_NOC;
                end
              end else if (active_task_q.opcode == OP_SOFTMAX) begin
                if (is_master) begin
                  gathered_tiles_q <= 1;
                  if (active_task_q.expected_tiles <= 1) begin
                    if (active_task_q.need_reduction) state_q <= ST_REDUCE;
                    else                              state_q <= ST_SOFTMAX;
                  end else begin
                    state_q <= ST_WAIT_GROUP_READY;
                  end
                end else begin
                  local_rd_ptr_q <= local_base_ptr_q;
                  send_beats_q   <= '0;
                  state_q        <= ST_SEND_NOC;
                end
              end else begin
                completion_pending_q <= 1'b1;
                state_q <= ST_COMPLETE;
              end
            end
          end
        end

        ST_SEND_NOC: begin
          if (egr_in_valid && egr_in_ready) begin
            send_beats_q   <= send_beats_q + 1'b1;
            local_rd_ptr_q <= local_rd_ptr_q + 1'b1;
            if (send_beats_q + 1'b1 >= tile_beats_q) begin
              state_q <= ST_WAIT_GROUP_READY;
            end
          end
        end

        ST_WAIT_GROUP_READY: begin
          if (ing_fire) begin
            if (is_master) begin
              if (gather_pkt_match) begin
                gather_wr_ptr_q <= gather_wr_ptr_q + 1'b1;
                result_acc_q    <= result_acc_q + ing_pkt.payload[31:0];
                if (ing_pkt.last) gathered_tiles_q <= gathered_tiles_q + 1'b1;
              end
            end else begin
              // Non-master ignores unrelated broadcasts and only retires on FINAL_BCAST.
              if (bcast_final_match) begin
                completion_pending_q <= 1'b1;
                state_q <= ST_COMPLETE;
              end
            end
          end

          if (is_master) begin
            if (grp_query_miss_i || !grp_query_hit_i || (grp_query_phase_i == GRP_PHASE_INVALID)) begin
              status_q <= STATUS_BAD_GROUP;
              completion_pending_q <= 1'b1;
              state_q <= ST_COMPLETE;
            end else if ((grp_query_master_sre_i == SRE_ID_W'(SRE_ID)) &&
                         grp_query_all_tiles_received_i &&
                         (gathered_tiles_q >= active_task_q.expected_tiles)) begin
              if (active_task_q.opcode == OP_REDUCTION) begin
                reduce_ctr_q <= '0;
                state_q      <= ST_REDUCE;
              end else if (active_task_q.opcode == OP_SOFTMAX) begin
                if (active_task_q.need_reduction) begin
                  reduce_ctr_q <= '0;
                  state_q      <= ST_REDUCE;
                end else begin
                  softmax_ctr_q <= '0;
                  state_q       <= ST_SOFTMAX;
                end
              end
            end
          end
        end

        ST_REDUCE: begin
          reduce_ctr_q <= reduce_ctr_q + 1'b1;
          result_acc_q <= result_acc_q + 32'd1;
          if (reduce_ctr_q + 1'b1 >= active_task_q.expected_tiles) begin
            if (is_master) begin
              grp_reduction_done_pulse <= 1'b1;
              grp_reduction_done_gid   <= active_task_q.group_id;
            end
            local_store_pending_q <= 1'b1;
            local_store_addr_q    <= local_base_ptr_q;
            local_store_data_q    <= {{(DATA_W-32){1'b0}}, result_acc_q};

            if (active_task_q.opcode == OP_SOFTMAX) begin
              softmax_ctr_q <= '0;
              state_q       <= ST_SOFTMAX;
            end else if (is_master && ((active_task_q.participant_mask & ~self_mask) != '0)) begin
              bcast_phase_q <= 1'b0;
              state_q       <= ST_BCAST_RESULT;
            end else begin
              completion_pending_q <= 1'b1;
              state_q <= ST_COMPLETE;
            end
          end
        end

        ST_SOFTMAX: begin
          softmax_ctr_q <= softmax_ctr_q + 1'b1;
          if (softmax_ctr_q == 16'd15) begin
            if (is_master) begin
              grp_softmax_done_pulse <= 1'b1;
              grp_softmax_done_gid   <= active_task_q.group_id;
            end
            local_store_pending_q <= 1'b1;
            local_store_addr_q    <= local_base_ptr_q;
            local_store_data_q    <= {{(DATA_W-32){1'b0}}, result_acc_q};

            if (is_master && ((active_task_q.participant_mask & ~self_mask) != '0)) begin
              bcast_phase_q <= 1'b0;
              state_q       <= ST_BCAST_RESULT;
            end else begin
              completion_pending_q <= 1'b1;
              state_q <= ST_COMPLETE;
            end
          end
        end

        ST_BCAST_RESULT: begin
          if (egr_in_valid && egr_in_ready) begin
            if (!bcast_phase_q) begin
              bcast_phase_q <= 1'b1;
            end else begin
              completion_pending_q <= 1'b1;
              state_q <= ST_COMPLETE;
            end
          end
        end

        ST_COMPLETE: begin
          if (done_valid && done_ready) begin
            completion_sent_q <= 1'b1;
            completion_pending_q <= 1'b0;

            if (reserved_valid_q) begin
              buf_used_bytes_q <= buf_used_bytes_q - reserved_bytes_q;
              reserved_valid_q <= 1'b0;
            end

            if (is_master && grouped_op) begin
              grp_close_pulse <= 1'b1;
              grp_close_gid   <= active_task_q.group_id;
            end

            state_q <= ST_IDLE;
          end
        end

        default: state_q <= ST_IDLE;
      endcase
    end
  end
endmodule
