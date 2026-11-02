`timescale 1ps/1ps
// Compare 256-bit MRF groups against the previous 64-bit models, and TCMs
// against their unchanged emitted memory definitions. No timing claim is made.
module ameooo_wide_sram_tb;
  reg wc=0, rc0=0, rc1=0, tc=0;
  reg [9:0] wa=0, ra0=0, ra1=0;
  reg we=0, re0=0, re1=0;
  reg [2047:0] wd=0;
  wire [2047:0] q0, q1, old0, old1;
  reg [6:0] ia=0;
  reg [10:0] da=0;
  reg ie=0, de=0, iw=0, dw=0;
  reg [63:0] im=0, dm=0;
  reg [511:0] id=0, dd=0;
  wire [511:0] iq, dq, iref, dref;
  integer comparisons=0, writes=0, reads=0, padding_checks=0;
  integer n,j,k;
  reg [31:0] rng=32'h2565a5e1;
  reg valid_i=0, valid_d=0;
  genvar g;
  generate
    for(g=0;g<8;g=g+1) begin: wide
      coral_mrf_768x256_1r1w a(ra0,re0,rc0,q0[g*256+:256],wa,we,wc,wd[g*256+:256]);
      coral_mrf_768x256_1r1w b(ra1,re1,rc1,q1[g*256+:256],wa,we,wc,wd[g*256+:256]);
    end
    for(g=0;g<32;g=g+1) begin: reference
      asap7_est_mrf_1024x64_1r1w a(.clk0(wc),.csb0(~(we && wa<768)),.addr0(wa),.din0(wd[g*64+:64]),
        .clk1(rc0),.csb1(~(re0 && ra0<768)),.addr1(ra0),.dout1(old0[g*64+:64]));
      asap7_est_mrf_1024x64_1r1w b(.clk0(wc),.csb0(~(we && wa<768)),.addr0(wa),.din0(wd[g*64+:64]),
        .clk1(rc1),.csb1(~(re1 && ra1<768)),.addr1(ra1),.dout1(old1[g*64+:64]));
    end
  endgenerate
  memory_128x512 it(ia,ie,tc,iw,id,iq,im);
  memory_2048x512 dt(da,de,tc,dw,dd,dq,dm);
  ref_memory_128x512 ir(ia,ie,tc,iw,id,iref,im);
  ref_memory_2048x512 dr(da,de,tc,dw,dd,dref,dm);
  function [31:0] random32;
    begin
      rng=rng^(rng<<13);rng=rng^(rng>>17);rng=rng^(rng<<5);random32=rng;
    end
  endfunction
  task random_data;
    integer z;
    begin
      for(z=0;z<64;z=z+1) wd[z*32+:32]=random32();
      for(z=0;z<16;z=z+1) begin id[z*32+:32]=random32();dd[z*32+:32]=random32();end
    end
  endtask
  task write_mrf;
    begin #2;wc=1;#2;wc=0;if(we && wa<768)writes=writes+1;end
  endtask
  task read_mrf0;
    begin
      #2;rc0=1;#2;
      if(re0 && ra0<768)begin
        if((^q0 === 1'bx) || q0!==old0)$fatal(1,"MRF0 mismatch addr=%0d",ra0);
        comparisons=comparisons+2048;reads=reads+1;
      end
      rc0=0;
    end
  endtask
  task read_mrf1;
    begin
      #2;rc1=1;#2;
      if(re1 && ra1<768)begin
        if((^q1 === 1'bx) || q1!==old1)$fatal(1,"MRF1 mismatch addr=%0d",ra1);
        comparisons=comparisons+2048;reads=reads+1;
      end
      rc1=0;
    end
  endtask
  task tcm_step;
    begin
      #2;tc=1;#2;
      if(ie && !iw)begin
        if((^iq === 1'bx)||iq!==iref)$fatal(1,"ITCM byte-mask/read mismatch addr=%0d",ia);
        comparisons=comparisons+512;
      end
      if(de && !dw)begin
        if((^dq === 1'bx)||dq!==dref)$fatal(1,"DTCM byte-mask/read mismatch addr=%0d",da);
        comparisons=comparisons+512;
      end
      tc=0;
    end
  endtask
  initial begin
    // Initialize every legal address in both read replicas and both TCMs.
    we=1;ie=1;de=1;iw=1;dw=1;im=~64'b0;dm=~64'b0;
    for(n=0;n<2048;n=n+1)begin
      wa=n%768;we=n<768;ia=n%128;ie=n<128;da=n;
      random_data();write_mrf();tcm_step();
    end
    we=0;ie=1;de=1;iw=0;dw=0;re0=1;re1=1;
    for(n=0;n<2048;n=n+1)begin
      ia=n%128;da=n;ra0=n%768;ra1=767-(n%768);
      read_mrf0();read_mrf1();tcm_step();
    end
    // Every byte enable and its complement, on both sides of bit 256.
    for(j=0;j<2;j=j+1)begin
      for(n=0;n<64;n=n+1)begin
        ia=j?127:0;da=j?2047:0;im=j?~(64'b1<<n):(64'b1<<n);dm=im;
        random_data();iw=1;dw=1;tcm_step();iw=0;dw=0;tcm_step();
      end
    end
    // Independent read clocks/addresses and common writes, plus TCM masking.
    for(n=0;n<12000;n=n+1)begin
      random_data();wa=random32()%768;ra0=random32()%768;ra1=random32()%768;
      we=random32()&1;re0=random32()&1;re1=random32()&1;
      if((n&1)==0)begin write_mrf();read_mrf0();read_mrf1();end
      else begin read_mrf1();read_mrf0();write_mrf();end
      // Also exercise simultaneous independent read/write clocks. Colliding
      // addresses are excluded from the system's qualified SRAM contract.
      if(wa==ra0)ra0=(ra0+1)%768;
      if(wa==ra1)ra1=(ra1+1)%768;
      #2;wc=1;rc0=1;rc1=1;#2;
      if(re0 && q0!==old0)$fatal(1,"Concurrent MRF0 mismatch");
      if(re1 && q1!==old1)$fatal(1,"Concurrent MRF1 mismatch");
      comparisons=comparisons+(re0+re1)*2048;
      wc=0;rc0=0;rc1=0;
      ia=random32()%128;da=random32()%2048;ie=random32()&1;de=random32()&1;
      iw=random32()&1;dw=random32()&1;im={random32(),random32()};dm={random32(),random32()};
      if((n&15)==0)begin im=0;dm=0;end
      if((n&15)==1)begin im=~64'b0;dm=~64'b0;end
      tcm_step();
    end
    // All padded words must be disabled in the actual grouped wrapper.
    re0=1;re1=1;we=1;
    for(n=768;n<1024;n=n+1)begin
      wa=n;ra0=n;ra1=n;#1;
      if(wide[0].a.u_memory.csb0!==1 || wide[0].a.u_memory.csb1!==1 ||
         wide[7].b.u_memory.csb0!==1 || wide[7].b.u_memory.csb1!==1)
        $fatal(1,"Padded address enabled: %0d",n);
      write_mrf();read_mrf0();read_mrf1();padding_checks=padding_checks+1;
    end
    we=0;ie=1;de=1;iw=0;dw=0;
    for(n=0;n<2048;n=n+1)begin
      ia=n%128;da=n;ra0=n%768;ra1=ra0;read_mrf0();read_mrf1();tcm_step();
      if(q0!==q1)$fatal(1,"Read replicas disagree");
    end
    $display("AMEOOO_WIDE_SRAM_PASS bit_comparisons=%0d writes=%0d independent_reads=%0d padding_checks=%0d seed=2565a5e1",comparisons,writes,reads,padding_checks);
    $finish;
  end
endmodule
