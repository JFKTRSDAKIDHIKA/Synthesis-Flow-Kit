// Generic synthesis factoring only. The flow proves this wrapper against the
// actual elaborated zvt_mt interface before use. Storage remains the original
// mapped register file; byte masks and higher-index writer priority are retained.
module GenusMtReadPort(input clk, input [32767:0] contents, input [3:0] bankIndex, slotIndex, output [127:0] data);
  wire [2047:0] bank = contents[bankIndex*2048 +: 2048];
  assign data = bank[slotIndex*128 +: 128];
endmodule
module GenusMtWriteBank(input clk, input [255:0] byteEn, input [63:0] bankDifference, slotIndex, input [2047:0] data, output reg [255:0] wen, output reg [2047:0] wdata);
  integer p,s;
  always @* begin
    wen = 0; wdata = 0;
    for(s=0;s<16;s=s+1) begin
      for(p=0;p<16;p=p+1) begin
        if ((|byteEn[p*16 +: 16]) && (bankDifference[p*4 +: 4] == 4'b0000) && (slotIndex[p*4 +: 4] == s[3:0])) begin
          wen[s*16 +: 16] = byteEn[p*16 +: 16]; wdata[s*128 +: 128] = data[p*128 +: 128];
        end
      end
    end
  end
endmodule
(* blackbox *) module MTStorage(input clk,rst_n, input [4095:0] wen, input [32767:0] wdata, output [32767:0] mt);
endmodule
module GenusMtWrapper(input clk,rst_n, input [63:0] readMtIdx,readSubIdx, output [2047:0] readData, input [255:0] writeEn,input [63:0] writeMtIdx,writeSubIdx,input [2047:0] writeData);
  wire [4095:0] wen;
  wire [32767:0] wdata,mt;
  MTStorage mtReg(clk,rst_n,wen,wdata,mt);
  genvar n;
  generate
    for(n=0;n<16;n=n+1) begin: reads
      GenusMtReadPort port(clk,mt,readMtIdx[n*4 +: 4],readSubIdx[n*4 +: 4],readData[n*128 +: 128]);
    end
    for(n=0;n<16;n=n+1) begin: banks
      localparam [3:0] BANK_ID = n;
      GenusMtWriteBank port(clk,writeEn,writeMtIdx ^ {16{BANK_ID}},writeSubIdx,writeData,wen[n*256 +: 256],wdata[n*2048 +: 2048]);
    end
  endgenerate
endmodule
