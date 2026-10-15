CC := arm-none-eabi-gcc
SIZE := arm-none-eabi-size
ROLE ?= controller
NODE_ID ?= 1
BUILD := build/$(ROLE)_$(NODE_ID)
FREERTOS_ROOT ?= /home/brain/freertos-kernel-export
FREERTOS_TCP_ROOT ?= /home/brain/freertos-tcp-export
TCP := $(FREERTOS_TCP_ROOT)/source
TARGET := $(BUILD)/brake_$(ROLE)_$(NODE_ID).axf
TCP_SOURCES := $(wildcard $(TCP)/*.c)
# The network stack and LAN9118 driver are used directly from the course's
# FreeRTOS checkout.  This project owns only the application task logic.
SOURCES := startup.c syscalls.c brake_network.c $(FREERTOS_ROOT)/tasks.c $(FREERTOS_ROOT)/list.c $(FREERTOS_ROOT)/queue.c $(FREERTOS_ROOT)/event_groups.c $(FREERTOS_ROOT)/portable/GCC/ARM_CM3/port.c $(FREERTOS_ROOT)/portable/MemMang/heap_3.c $(TCP_SOURCES) $(TCP)/portable/BufferManagement/BufferAllocation_2.c $(TCP)/portable/NetworkInterface/MPS2_AN385/NetworkInterface.c $(TCP)/portable/NetworkInterface/MPS2_AN385/ether_lan9118/smsc9220_eth_drv.c
OBJECTS := $(patsubst %.c,$(BUILD)/%.o,$(SOURCES))
CFLAGS := -mthumb -mcpu=cortex-m3 -Os -ffunction-sections -fdata-sections -Wall -Wextra -Wshadow -MMD -DROLE_$(ROLE)=1 -DNODE_ID=$(NODE_ID)
CFLAGS += -I. -ICMSIS -I$(FREERTOS_ROOT)/include -I$(FREERTOS_ROOT)/portable/GCC/ARM_CM3 -I$(TCP)/include -I$(TCP)/portable/NetworkInterface/MPS2_AN385/ether_lan9118 -I$(TCP)/portable/Compiler/GCC
LDFLAGS := -T mps2_m3.ld -Xlinker -Map=$(BUILD)/$(ROLE)_$(NODE_ID).map -Xlinker --gc-sections -nostartfiles -specs=nano.specs -specs=nosys.specs -specs=rdimon.specs
.PHONY: all clean
all: $(TARGET)
$(TARGET): $(OBJECTS)
	$(CC) $(CFLAGS) $(LDFLAGS) $^ -o $@
	$(SIZE) $@
$(BUILD)/%.o: %.c Makefile
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@
-include $(OBJECTS:.o=.d)
clean:
	rm -rf $(BUILD)
