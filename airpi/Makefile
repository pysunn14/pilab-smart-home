CC ?= cc
PKG_CONFIG ?= pkg-config
BUILD_DIR ?= build
CFLAGS ?= -O2

COMMON_BSW = ../common/bsw/services
.DEFAULT_GOAL := air-node
include $(COMMON_BSW)/websocket.mk
include $(COMMON_BSW)/telemetry.mk
SOURCES = air_node.c air_room.c air_servo.c $(COMMON_BSW)/virtual_actuator.c
HEADERS = air_room.h air_servo.h $(COMMON_BSW)/virtual_actuator.h $(TELEMETRY_HEADER) $(WEBSOCKET_HEADER)

AIR_NODE = $(call escape_build_path,$(BUILD_DIR)/air-node)

.PHONY: air-node
air-node: $(AIR_NODE)

$(AIR_NODE): $(SOURCES) $(HEADERS) $(TELEMETRY_ARCHIVE_TARGET) $(WEBSOCKET_ARCHIVE_TARGET)
	mkdir -p "$(BUILD_DIR)"
	$(CC) $(CPPFLAGS) -I$(COMMON_BSW) $(CFLAGS) -std=c11 -Wall -Wextra -Werror -pthread \
	    $$($(PKG_CONFIG) --cflags libcurl json-c) $(SOURCES) $(LDFLAGS) $(LDLIBS) "$(TELEMETRY_ARCHIVE)" "$(WEBSOCKET_ARCHIVE)" \
	    $$($(PKG_CONFIG) --libs libcurl json-c) -o "$@"
