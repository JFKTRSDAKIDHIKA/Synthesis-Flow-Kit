SHELL := /bin/bash
PROJ_PATH := $(shell pwd)

# User-configurable knobs (CLI override)
DESIGN ?= gcd
export CLK_FREQ_MHZ ?= 500
PDK ?= nangate45

# Array parameters
export ARRAY_M ?= 4
export ARRAY_N ?= 4

# Provide these from CLI or uncomment defaults if you want:
# SDC_FILE  ?= $(PROJ_PATH)/example/$(DESIGN).sdc
# RTL_FILES ?= $(shell find $(PROJ_PATH)/example -name "*.v")

# Tool selection
#   make syn SYN_TOOL=dc|yosys
#   make sta STA_TOOL=pt|opensta
SYN_TOOL ?= dc
STA_TOOL ?= pt

# Tools
YOSYS     ?= yosys
OPENSTA   ?= sta
DC_SHELL  ?= dc_shell-t
PT_SHELL  ?= pt_shell
LC_SHELL  ?= lc_shell

# Directories / scripts
SCRIPT_DIR := $(PROJ_PATH)/scripts
RESULT_DIR := $(PROJ_PATH)/result/$(DESIGN)-$(PDK)-$(CLK_FREQ_MHZ)MHz-M$(ARRAY_M)-N$(ARRAY_N)

YOSYS_SCRIPT ?= $(SCRIPT_DIR)/yosys.tcl
DC_SCRIPT    ?= $(SCRIPT_DIR)/dc.tcl
STA_SCRIPT   ?= $(SCRIPT_DIR)/opensta.tcl
PT_SCRIPT    ?= $(SCRIPT_DIR)/pt.tcl

# Outputs
NETLIST_SYN_V   := $(RESULT_DIR)/$(DESIGN).netlist.syn.v
NETLIST_FIXED_V := $(RESULT_DIR)/$(DESIGN).netlist.fixed.v

# IMPORTANT:
# - OpenSTA report (kept for optional use)
OPENSTA_RPT := $(RESULT_DIR)/$(DESIGN).opensta.rpt
# - PrimeTime report (the one you actually use)
PT_RPT      := $(RESULT_DIR)/$(DESIGN).pt.rpt

# Extra DC outputs (optional but useful)
DC_LOG        := $(RESULT_DIR)/dc.log
DC_QOR_RPT    := $(RESULT_DIR)/dc.qor.rpt
DC_AREA_RPT   := $(RESULT_DIR)/dc.area.rpt
DC_TIMING_RPT := $(RESULT_DIR)/dc.timing.rpt

# PDK mapping (PDK -> LIB_FILE / LIB_DBS)
#   - LIB_FILE: .lib (mainly for OpenSTA)
#   - LIB_DBS : .db  (for DC/PT)
ASAP7_LIB     := $(PROJ_PATH)/pdk/asap7sc7p5t_28/LIB/asap7_7nm_RVT_TT.lib
ASAP7_DB_DIR  := $(PROJ_PATH)/pdk/asap7sc7p5t_28/LIB/DB
ASAP7_DBS_RVT_TT := \
  $(ASAP7_DB_DIR)/asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.db \
  $(ASAP7_DB_DIR)/asap7sc7p5t_INVBUF_RVT_TT_nldm_211120.db \
  $(ASAP7_DB_DIR)/asap7sc7p5t_AO_RVT_TT_nldm_211120.db \
  $(ASAP7_DB_DIR)/asap7sc7p5t_OA_RVT_TT_nldm_211120.db \
  $(ASAP7_DB_DIR)/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.db

NANGATE45_LIB := $(PROJ_PATH)/pdk/nangate45/lib/Nangate45_typ.lib
NANGATE45_DB  := $(PROJ_PATH)/pdk/nangate45/lib/Nangate45_typ.db

ifeq ($(PDK),asap7sc7p5t_28)
  LIB_FILE := $(ASAP7_LIB)
  LIB_DBS  := $(ASAP7_DBS_RVT_TT)
else ifeq ($(PDK),nangate45)
  LIB_FILE := $(NANGATE45_LIB)
  LIB_DBS  := $(NANGATE45_DB)
else
  $(error Unsupported PDK '$(PDK)'. Supported: asap7sc7p5t_28, nangate45)
endif

# Fail-fast: .lib must exist if OpenSTA is used. We keep a soft check here and hard-check inside sta-opensta.
ifeq ($(wildcard $(LIB_FILE)),)
  $(warning LIB_FILE not found: $(LIB_FILE))
endif

# Fail-fast for .db usage (DC/PT)
# Only warn at parse time; hard-check when running targets.
$(foreach f,$(LIB_DBS),$(if $(wildcard $(f)),,$(warning LIB DB not found: $(f))))

# STA netlist selection policy
#   Prefer fixed netlist if exists; otherwise use syn netlist.
STA_NETLIST ?= $(if $(wildcard $(NETLIST_FIXED_V)),$(NETLIST_FIXED_V),$(NETLIST_SYN_V))

# Helper: printing
print-config:
	@echo "DESIGN         = $(DESIGN)"
	@echo "PDK            = $(PDK)"
	@echo "CLK_FREQ_MHZ   = $(CLK_FREQ_MHZ)"
	@echo "SYN_TOOL       = $(SYN_TOOL)"
	@echo "STA_TOOL       = $(STA_TOOL)"
	@echo "SDC_FILE       = $(SDC_FILE)"
	@echo "RTL_FILES      = $(RTL_FILES)"
	@echo "RESULT_DIR     = $(RESULT_DIR)"
	@echo "NETLIST_SYN_V  = $(NETLIST_SYN_V)"
	@echo "NETLIST_FIXED_V= $(NETLIST_FIXED_V)"
	@echo "STA_NETLIST    = $(STA_NETLIST)"
	@echo "LIB_FILE       = $(LIB_FILE)"
	@echo "LIB_DBS        = $(LIB_DBS)"
	@echo "OPENSTA_RPT    = $(OPENSTA_RPT)"
	@echo "PT_RPT         = $(PT_RPT)"
.PHONY: print-config

# Sanity checks (targets)
check-rtl:
	@if [ -z "$(RTL_FILES)" ]; then \
	  echo "ERROR: RTL_FILES is empty. Provide RTL_FILES=... or uncomment default RTL_FILES." >&2; \
	  exit 2; \
	fi
.PHONY: check-rtl

check-sdc:
	@if [ -z "$(SDC_FILE)" ]; then \
	  echo "ERROR: SDC_FILE is empty. Provide SDC_FILE=... ." >&2; \
	  exit 2; \
	fi
	@if [ ! -f "$(SDC_FILE)" ]; then \
	  echo "ERROR: SDC_FILE not found: $(SDC_FILE)" >&2; \
	  exit 2; \
	fi
.PHONY: check-sdc

check-netlist:
	@if [ ! -f "$(STA_NETLIST)" ]; then \
	  echo "ERROR: STA_NETLIST not found: $(STA_NETLIST)" >&2; \
	  echo "Hint: run 'make syn' first (or 'make fix-fanout'), or override STA_NETLIST=..." >&2; \
	  exit 2; \
	fi
.PHONY: check-netlist

check-lib-db:
	@missing=0; \
	for f in $(LIB_DBS); do \
	  if [ ! -f "$$f" ]; then \
	    missing=1; \
	  fi; \
	done; \
	if [ $$missing -ne 0 ] && [ "$(PDK)" = "nangate45" ]; then \
	  $(MAKE) gen-lib-db PDK=$(PDK); \
	fi; \
	for f in $(LIB_DBS); do \
	  if [ ! -f "$$f" ]; then \
	    echo "ERROR: LIB DB not found: $$f" >&2; \
	    missing=1; \
	  fi; \
	done; \
	if [ $$missing -ne 0 ]; then exit 2; fi
.PHONY: check-lib-db

gen-lib-db:
ifeq ($(PDK),nangate45)
	@echo "Generating Nangate45 .db from $(NANGATE45_LIB)..."
	@$(LC_SHELL) -f $(SCRIPT_DIR)/nangate45_lib_to_db.tcl \
	-x "set LIB_FILE {$(abspath $(NANGATE45_LIB))}; \
	    set OUT_DB {$(abspath $(NANGATE45_DB))};"
else
	@echo "ERROR: gen-lib-db is only implemented for PDK=nangate45" >&2
	@exit 2
endif
.PHONY: gen-lib-db

check-lib-lib:
	@if [ ! -f "$(LIB_FILE)" ]; then \
	  echo "ERROR: LIB_FILE not found: $(LIB_FILE)" >&2; \
	  exit 2; \
	fi
.PHONY: check-lib-lib

# Targets

init:
	bash -c "$$(wget -O - https://ysyx.oscc.cc/slides/resources/scripts/init-yosys-sta.sh)"
.PHONY: init

# Unified synthesis entry
syn: syn-$(SYN_TOOL)
.PHONY: syn syn-yosys syn-dc

# Synthesis rule: ensure only ONE recipe exists for NETLIST_SYN_V
# by guarding with ifeq on SYN_TOOL (avoids overriding warnings).

ifeq ($(SYN_TOOL),yosys)

syn-yosys: $(NETLIST_SYN_V)

$(NETLIST_SYN_V): check-rtl $(YOSYS_SCRIPT) $(RTL_FILES)
	@mkdir -p $(@D)
	@echo "Running Yosys synth..."
	@echo tcl $(YOSYS_SCRIPT) $(DESIGN) $(PDK) \"$(RTL_FILES)\" $@ | $(YOSYS) -l $(@D)/yosys.log -s -

else ifeq ($(SYN_TOOL),dc)

syn-dc: $(NETLIST_SYN_V)

$(NETLIST_SYN_V): check-rtl check-sdc check-lib-db $(DC_SCRIPT) $(RTL_FILES)
	@mkdir -p $(@D)
	@echo "Running Design Compiler synth..."
	@cd $(@D) && $(DC_SHELL) -f $(DC_SCRIPT) \
	-output_log_file dc.log \
	-x "set DESIGN {$(DESIGN)}; \
		set PDK {$(PDK)}; \
		set RTL_FILES_RAW {$(abspath $(RTL_FILES))}; \
		set SDC_FILE {$(abspath $(SDC_FILE))}; \
		set OUT_NETLIST {$(abspath $(NETLIST_SYN_V))}; \
		set CLK_FREQ_MHZ {$(CLK_FREQ_MHZ)}; \
		set LIB_DBS_RAW {$(strip $(LIB_DBS))}; \
		set RESULT_DIR {$(abspath $(@D))};"

else
  $(error Unsupported SYN_TOOL '$(SYN_TOOL)'. Supported: dc, yosys)
endif

# STA entry
sta: sta-$(STA_TOOL)
.PHONY: sta sta-opensta sta-pt

# ---- OpenSTA 
sta-opensta: $(OPENSTA_RPT)

$(OPENSTA_RPT): check-sdc check-netlist check-lib-lib $(STA_SCRIPT)
	@mkdir -p $(@D)
	@echo "Running OpenSTA..."
	@DESIGN=$(DESIGN) \
	LIB_FILE=$(LIB_FILE) \
	STA_NETLIST=$(STA_NETLIST) \
	SDC_FILE=$(SDC_FILE) \
	CLK_FREQ_MHZ=$(CLK_FREQ_MHZ) \
	TIMING_RPT=$@ \
	$(OPENSTA) -exit $(STA_SCRIPT) 2>&1 | tee $(RESULT_DIR)/opensta.log

# ---- PrimeTime 
sta-pt: $(PT_RPT)

$(PT_RPT): check-sdc check-netlist check-lib-db $(PT_SCRIPT)
	@mkdir -p $(RESULT_DIR)
	@echo "Running PrimeTime..."
	@cd $(RESULT_DIR) && $(PT_SHELL) -f $(PT_SCRIPT) \
	-output_log_file pt.log \
	-x "set DESIGN {$(DESIGN)}; \
	    set SDC_FILE {$(abspath $(SDC_FILE))}; \
	    set STA_NETLIST {$(abspath $(STA_NETLIST))}; \
	    set CLK_FREQ_MHZ {$(CLK_FREQ_MHZ)}; \
	    set TIMING_RPT {$(abspath $(PT_RPT))}; \
	    set RESULT_DIR {$(abspath $(RESULT_DIR))}; \
	    set LIB_DBS_RAW {$(strip $(LIB_DBS))};"

clean:
	-rm -rf result/ vsrc/generated
.PHONY: clean
