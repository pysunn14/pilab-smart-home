CC ?= cc
PKG_CONFIG ?= pkg-config
BUILD_DIR ?= build
CFLAGS ?= -O2

COMMON_BSW = ../common/bsw/services
.DEFAULT_GOAL := button-monitor
include $(COMMON_BSW)/websocket.mk
INCLUDES = -I$(COMMON_BSW) -Ibsw/ecu_abstraction -Ibsw/services -Ibsw/mcal -Iapplication -Irte
SOURCES = button_monitor.c bsw/ecu_abstraction/button_debounce.c \
          bsw/services/button_input_task.c bsw/services/button_input_linux.c \
          bsw/services/button_tx_queue.c bsw/services/button_websocket.c \
          bsw/mcal/button_gpio_input.c application/button_swc.c rte/button_rte.c
HEADERS = $(WEBSOCKET_HEADER) $(wildcard bsw/*/*.h application/*.h rte/*.h)

BUTTON_MONITOR = $(call escape_build_path,$(BUILD_DIR)/button-monitor)

.PHONY: button-monitor
button-monitor: $(BUTTON_MONITOR)

$(BUTTON_MONITOR): $(SOURCES) $(HEADERS) $(WEBSOCKET_ARCHIVE_TARGET)
	$(PKG_CONFIG) --atleast-version=2.0 libgpiod
	mkdir -p "$(BUILD_DIR)"
	$(CC) $(CPPFLAGS) $(INCLUDES) $(CFLAGS) -std=c11 -Wall -Wextra -Werror -pthread \
	    $$($(PKG_CONFIG) --cflags libgpiod libcurl) $(SOURCES) $(LDFLAGS) $(LDLIBS) "$(WEBSOCKET_ARCHIVE)" \
	    $$($(PKG_CONFIG) --libs libgpiod libcurl) -o "$@"
