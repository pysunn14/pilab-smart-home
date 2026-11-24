CC ?= cc
PKG_CONFIG ?= pkg-config
BUILD_DIR ?= build
CFLAGS ?= -O2

PACKAGES = libgpiod libcurl json-c
COMMON_BSW = ../common/bsw/services
.DEFAULT_GOAL := light-node
include $(COMMON_BSW)/websocket.mk
include $(COMMON_BSW)/telemetry.mk
INCLUDES = -I$(COMMON_BSW) -Iapplication -Irte -Ibsw/mcal -Ibsw/services
SOURCES = light_node.c application/light_swc.c rte/light_rte.c \
          bsw/mcal/light_gpio_output.c bsw/services/light_websocket.c bsw/services/light_protocol.c \
          bsw/services/light_sensor_input.c application/blind_swc.c $(COMMON_BSW)/virtual_actuator.c
HEADERS = $(WEBSOCKET_HEADER) $(TELEMETRY_HEADER) $(COMMON_BSW)/virtual_actuator.h \
          $(wildcard application/*.h rte/*.h bsw/*/*.h)

LIGHT_NODE = $(call escape_build_path,$(BUILD_DIR)/light-node)

.PHONY: light-node
light-node: $(LIGHT_NODE)

$(LIGHT_NODE): $(SOURCES) $(HEADERS) $(TELEMETRY_ARCHIVE_TARGET) $(WEBSOCKET_ARCHIVE_TARGET)
	$(PKG_CONFIG) --atleast-version=2.0 libgpiod
	$(PKG_CONFIG) --atleast-version=8.14.0 libcurl
	$(PKG_CONFIG) --atleast-version=0.15 json-c
	mkdir -p "$(BUILD_DIR)"
	$(CC) $(CPPFLAGS) $(INCLUDES) $(CFLAGS) -std=c11 -Wall -Wextra -Werror \
	    $$($(PKG_CONFIG) --cflags $(PACKAGES)) $(SOURCES) $(LDFLAGS) $(LDLIBS) "$(TELEMETRY_ARCHIVE)" "$(WEBSOCKET_ARCHIVE)" \
	    $$($(PKG_CONFIG) --libs $(PACKAGES)) -o "$@"
