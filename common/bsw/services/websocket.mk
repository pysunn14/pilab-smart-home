# Build beside the caller's executable so different toolchains can use separate build directories.
websocket_empty :=
websocket_space := $(websocket_empty) $(websocket_empty)
escape_build_path = $(subst $(websocket_space),\$(websocket_space),$(1))

WEBSOCKET_ARCHIVE = $(BUILD_DIR)/libpilab_websocket.a
WEBSOCKET_ARCHIVE_TARGET = $(call escape_build_path,$(WEBSOCKET_ARCHIVE))
WEBSOCKET_OBJECT = $(call escape_build_path,$(BUILD_DIR)/websocket_transport.o)
WEBSOCKET_HEADER = $(COMMON_BSW)/websocket_transport.h

$(WEBSOCKET_OBJECT): $(COMMON_BSW)/websocket_transport.c $(WEBSOCKET_HEADER) $(COMMON_BSW)/websocket.mk
	$(PKG_CONFIG) --atleast-version=7.86.0 libcurl
	mkdir -p "$(BUILD_DIR)"
	$(CC) $(CPPFLAGS) $(CFLAGS) -std=c11 -Wall -Wextra -Werror \
	    $$($(PKG_CONFIG) --cflags libcurl) -c "$<" -o "$@"

$(WEBSOCKET_ARCHIVE_TARGET): $(WEBSOCKET_OBJECT)
	$(AR) rcs "$@" "$<"
