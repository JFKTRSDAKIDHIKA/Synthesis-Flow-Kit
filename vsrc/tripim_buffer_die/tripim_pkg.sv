package tripim_pkg;
  parameter int MAX_SRES      = 32;
  parameter int TASK_ID_W     = 16;
  parameter int TAG_W         = 16;
  parameter int GROUP_ID_W    = 8;
  parameter int SRE_ID_W      = 8;
  parameter int TILE_BYTES_W  = 16;
  parameter int TILE_CNT_W    = 8;
  parameter int CHANNEL_W     = 4;
  parameter int BANK_W        = 4;
  parameter int STATUS_W      = 3;

  typedef enum logic [3:0] {
    OP_DMA_H2D   = 4'd0,
    OP_SOFTMAX   = 4'd1,
    OP_REDUCTION = 4'd2,
    OP_TILE_MOVE = 4'd3
  } opcode_e;

  typedef enum logic [STATUS_W-1:0] {
    STATUS_OK              = 3'd0,
    STATUS_BAD_GROUP       = 3'd1,
    STATUS_BAD_HB_METADATA = 3'd2,
    STATUS_BUFFER_OVERFLOW = 3'd3,
    STATUS_BAD_OPCODE      = 3'd4,
    STATUS_BAD_DST         = 3'd5
  } status_e;

  typedef enum logic [2:0] {
    PKT_TILE_DATA        = 3'd0,
    PKT_REDUCTION_RESULT = 3'd1,
    PKT_SOFTMAX_RESULT   = 3'd2,
    PKT_FINAL_BCAST      = 3'd3
  } noc_pkt_type_e;

  typedef enum logic [2:0] {
    GRP_PHASE_INVALID = 3'd0,
    GRP_PHASE_COLLECT = 3'd1,
    GRP_PHASE_REDUCE  = 3'd2,
    GRP_PHASE_SOFTMAX = 3'd3,
    GRP_PHASE_DONE    = 3'd4
  } group_phase_e;

  typedef struct packed {
    logic                       valid;
    logic [TASK_ID_W-1:0]       task_id;
    opcode_e                    opcode;
    logic [TAG_W-1:0]           tag;
    logic [GROUP_ID_W-1:0]      group_id;
    logic [SRE_ID_W-1:0]        src_sre_id;       // master id for grouped ops
    logic [SRE_ID_W-1:0]        dst_sre_id;       // assigned execution SRE
    logic [CHANNEL_W-1:0]       channel_id;
    logic [BANK_W-1:0]          bank_id;
    logic [MAX_SRES-1:0]        participant_mask;
    logic [TILE_BYTES_W-1:0]    tile_bytes;
    logic                       need_reduction;
    logic [TILE_CNT_W-1:0]      expected_tiles;
  } task_desc_t;

  typedef struct packed {
    logic [TASK_ID_W-1:0] task_id;
    opcode_e              opcode;
    logic [TAG_W-1:0]     tag;
    status_e              status;
  } completion_event_t;

  typedef struct packed {
    noc_pkt_type_e              pkt_type;
    opcode_e                    opcode;
    logic [TASK_ID_W-1:0]       task_id;
    logic [GROUP_ID_W-1:0]      group_id;
    logic [SRE_ID_W-1:0]        src_sre_id;
    logic [SRE_ID_W-1:0]        dst_sre_id;
    logic                       bcast;
    logic [MAX_SRES-1:0]        bcast_mask;
    logic                       last;
    logic [63:0]                payload;
  } noc_pkt_t;

  function automatic logic opcode_supported(opcode_e op);
    opcode_supported = (op == OP_DMA_H2D) ||
                       (op == OP_SOFTMAX) ||
                       (op == OP_REDUCTION) ||
                       (op == OP_TILE_MOVE);
  endfunction
endpackage
