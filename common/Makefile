BUILD_DIR ?= build
CC ?= cc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -O2

COMMON_BSW = bsw/services
.DEFAULT_GOAL := websocket
include $(COMMON_BSW)/websocket.mk

.PHONY: websocket
websocket: $(WEBSOCKET_ARCHIVE_TARGET)
