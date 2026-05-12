SHELL := /bin/bash
SRC_DIR := src
BUILD_DIR := build
JSONNET ?= jsonnet
JB ?= jb

SOURCES := $(wildcard $(SRC_DIR)/*.jsonnet)
TARGETS := $(SOURCES:$(SRC_DIR)/%.jsonnet=$(BUILD_DIR)/%.json)

.PHONY: all dashboards vendor clean validate

all: dashboards

dashboards: vendor $(BUILD_DIR) $(TARGETS)

vendor:
	@if [ ! -d vendor ]; then $(JB) install; fi

$(BUILD_DIR):
	@mkdir -p $@

$(BUILD_DIR)/%.json: $(SRC_DIR)/%.jsonnet | $(BUILD_DIR)
	@echo "[jsonnet] $< → $@"
	@$(JSONNET) -J vendor -J vendor/grafonnet -J $(SRC_DIR) $< | python3 -c "import json,sys; json.dump(json.load(sys.stdin), open('$@','w'), indent=2, sort_keys=True)"
	@echo "  ✓ $@"

validate: dashboards
	@for f in $(TARGETS); do \
	  python3 -c "import json,sys; d=json.load(open('$$f')); panels=d.get('panels',[]); assert panels, f'no panels in $$f'; [ (lambda p: __import__('sys').exit(f'panel {p.get(\"id\")} empty targets in $$f') if not p.get('targets') else None)(p) for p in panels ]"; \
	  echo "  ✓ validated $$f"; \
	done

clean:
	rm -rf $(BUILD_DIR)
