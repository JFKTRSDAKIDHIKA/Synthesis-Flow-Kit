module tripim_dma_engine #(
  parameter int DATA_W            = 64,
  parameter int CMD_FIFO_DEPTH    = 8,
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

  output logic                                dma_wr_valid,
  input  logic                                dma_wr_ready,
  output logic [tripim_pkg::SRE_ID_W-1:0]     dma_wr_sre_id,
  output logic [DMA_ADDR_W-1:0]               dma_wr_addr,
  output logic [DATA_W-1:0]                   dma_wr_data,
  output logic                                dma_wr_last,
  output logic [tripim_pkg::TASK_ID_W-1:0]    dma_wr_task_id,

  output logic                           done_valid,
  input  logic                           done_ready,
  output tripim_pkg::completion_event_t done_event
);
  import tripim_pkg::*;

  localparam int BEAT_BYTES = DATA_W/8;

  typedef enum logic [1:0] {
    DMA_IDLE,
    DMA_WAIT_HB,
    DMA_COMPLETE
  } dma_state_e;

  dma_state_e state_q;
  task_desc_t active_task_q;

  logic q_out_valid, q_out_ready;
  logic [$bits(task_desc_t)-1:0] q_out_bits;
  task_desc_t q_head;

  logic [TILE_BYTES_W-1:0] recv_bytes_q;
  logic [DMA_ADDR_W-1:0] wr_addr_q;
  logic hb_meta_ok;
  logic bad_hb_meta_q;
  logic completion_pending_q;
  logic completion_sent_q;

  assign q_head = task_desc_t'(q_out_bits);

  tripim_task_fifo #(
    .WIDTH($bits(task_desc_t)),
    .DEPTH(CMD_FIFO_DEPTH)
  ) u_dma_cmdq (
    .clk      (clk),
    .rst_n    (rst_n),
    .in_valid (task_in_valid),
    .in_ready (task_in_ready),
    .in_data  (task_in_desc),
    .out_valid(q_out_valid),
    .out_ready(q_out_ready),
    .out_data (q_out_bits),
    .level    ()
  );

  assign q_out_ready = (state_q == DMA_IDLE) && q_out_valid;

  assign hb_meta_ok = (hb_opcode == OP_DMA_H2D) &&
                      (hb_task_id == active_task_q.task_id) &&
                      (hb_dst_sre_id == active_task_q.dst_sre_id) &&
                      (hb_group_id == active_task_q.group_id);

  assign dma_wr_valid   = (state_q == DMA_WAIT_HB) && hb_in_valid && hb_in_ready && hb_meta_ok;
  assign dma_wr_sre_id  = active_task_q.dst_sre_id;
  assign dma_wr_addr    = wr_addr_q;
  assign dma_wr_data    = hb_in_data;
  assign dma_wr_last    = hb_in_last;
  assign dma_wr_task_id = active_task_q.task_id;

  always_comb begin
    hb_in_ready = 1'b0;
    if (state_q == DMA_WAIT_HB) begin
      if (!hb_meta_ok) hb_in_ready = 1'b1;
      else             hb_in_ready = dma_wr_ready;
    end

    done_valid = completion_pending_q && !completion_sent_q;
    done_event.task_id = active_task_q.task_id;
    done_event.opcode  = active_task_q.opcode;
    done_event.tag     = active_task_q.tag;
    if (bad_hb_meta_q) done_event.status = STATUS_BAD_HB_METADATA;
    else               done_event.status = STATUS_OK;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_q       <= DMA_IDLE;
      active_task_q <= '0;
      recv_bytes_q  <= '0;
      wr_addr_q     <= '0;
      bad_hb_meta_q <= 1'b0;
      completion_pending_q <= 1'b0;
      completion_sent_q    <= 1'b0;
    end else begin
      case (state_q)
        DMA_IDLE: begin
          if (q_out_valid) begin
            active_task_q <= q_head;
            recv_bytes_q  <= '0;
            wr_addr_q     <= '0;
            bad_hb_meta_q <= 1'b0;
            completion_pending_q <= 1'b0;
            completion_sent_q    <= 1'b0;
            state_q       <= DMA_WAIT_HB;
          end
        end

        DMA_WAIT_HB: begin
          if (hb_in_valid && hb_in_ready) begin
            recv_bytes_q <= recv_bytes_q + BEAT_BYTES;
            if (hb_meta_ok) wr_addr_q <= wr_addr_q + 1'b1;
            else            bad_hb_meta_q <= 1'b1;

            if (hb_in_last || (recv_bytes_q + BEAT_BYTES >= active_task_q.tile_bytes)) begin
              completion_pending_q <= 1'b1;
              state_q <= DMA_COMPLETE;
            end
          end
        end

        DMA_COMPLETE: begin
          if (done_valid && done_ready) begin
            completion_sent_q    <= 1'b1;
            completion_pending_q <= 1'b0;
            state_q <= DMA_IDLE;
          end
        end

        default: state_q <= DMA_IDLE;
      endcase
    end
  end
endmodule
