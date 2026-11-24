TELEMETRY_ARCHIVE = $(BUILD_DIR)/libpilab_telemetry.a
TELEMETRY_ARCHIVE_TARGET = $(call escape_build_path,$(TELEMETRY_ARCHIVE))
TELEMETRY_OBJECT = $(call escape_build_path,$(BUILD_DIR)/telemetry_publisher.o)
TELEMETRY_HEADER = $(COMMON_BSW)/telemetry_publisher.h

$(TELEMETRY_OBJECT): $(COMMON_BSW)/telemetry_publisher.c $(TELEMETRY_HEADER) $(WEBSOCKET_HEADER) $(COMMON_BSW)/telemetry.mk
	mkdir -p "$(BUILD_DIR)"
	$(CC) $(CPPFLAGS) $(CFLAGS) -std=c11 -Wall -Wextra -Werror \
	    $$($(PKG_CONFIG) --cflags libcurl) -c "$<" -o "$@"

$(TELEMETRY_ARCHIVE_TARGET): $(TELEMETRY_OBJECT)
	$(AR) rcs "$@" "$<"
