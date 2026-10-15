#include "FreeRTOS.h"
#include "FreeRTOS_IP.h"
#include "FreeRTOS_Sockets.h"
#include "queue.h"
#include "task.h"
#include "smsc9220_eth_drv.h"
#include "SMM_MPS2.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define UDP_PORT 5000U
#define GROUP_TEXT "239.255.0.1"
#define QUEUE_CAPACITY 1024U

typedef struct { uint32_t sequence; uint32_t source_tick; char kind[8]; } Command_t;
static const uint8_t ip[4] = {10U,10U,0U,(uint8_t)(10U + NODE_ID)};
static const uint8_t mask[4] = {255U,255U,255U,0U};
static const uint8_t zero[4] = {0U,0U,0U,0U};
const uint8_t ucMACAddress[6] = {0x52U,0x54U,0U,0x12U,0x34U,(uint8_t)(0xA0U + NODE_ID)};
static QueueHandle_t brake_queue, normal_queue;

/* Inherited LAN9118 multicast admission fix. */
#define LAN9118_MAC_CR_PRMS (1UL << 18U)
#define LAN9118_MAC_CR_MCPAS (1UL << 19U)
static const struct smsc9220_eth_dev_cfg_t nic_cfg = { SMSC9220_BASE };
static struct smsc9220_eth_dev_data_t nic_data = { 0 };
static const struct smsc9220_eth_dev_t nic = { &nic_cfg, &nic_data };

static void enable_multicast_receive(void)
{
    uint32_t control = 0U;
    configASSERT(smsc9220_mac_regread(&nic, SMSC9220_MAC_REG_OFFSET_CR, &control) == SMSC9220_ERROR_NONE);
    control |= LAN9118_MAC_CR_PRMS | LAN9118_MAC_CR_MCPAS;
    configASSERT(smsc9220_mac_regwrite(&nic, SMSC9220_MAC_REG_OFFSET_CR, control) == SMSC9220_ERROR_NONE);
}

extern int uart_getc_nonblocking(void);
void vLoggingPrintf(const char *format, ...)
{
    va_list args; va_start(args, format); vprintf(format, args); va_end(args);
}

typedef struct { TickType_t tick; char task[10]; } SwitchTrace_t;
#define SWITCH_TRACE_CAPACITY 4096U
static volatile SwitchTrace_t switch_trace[SWITCH_TRACE_CAPACITY];
static volatile uint32_t switch_trace_write = 0U;
static uint32_t switch_trace_read = 0U;

void vTaskSwitchTraceIn(void)
{
    uint32_t slot = switch_trace_write++ % SWITCH_TRACE_CAPACITY;
    char *name = pcTaskGetName(NULL);
    uint32_t i;
    switch_trace[slot].tick = xTaskGetTickCount();
    for(i = 0U; i < sizeof(switch_trace[slot].task) - 1U && name[i] != '\0'; ++i) switch_trace[slot].task[i] = name[i];
    switch_trace[slot].task[i] = '\0';
}

static void flush_switch_trace(void)
{
    while(switch_trace_read != switch_trace_write)
    {
        uint32_t slot = switch_trace_read++ % SWITCH_TRACE_CAPACITY;
        printf("TRACE node=ECU-%u task=%s state=SWITCH_IN tick=%lu\n", NODE_ID, switch_trace[slot].task, (unsigned long)switch_trace[slot].tick);
    }
}

static uint32_t random_state = 0x7A190001U + NODE_ID;
static uint32_t random_value(void)
{
    random_state = random_state * 1664525U + 1013904223U;
    return random_state;
}

static void event(const char *task, unsigned priority, const char *state)
{
    printf("EVT node=ECU-%u task=%s prio=%u state=%s tick=%lu\n", NODE_ID, task, priority, state, (unsigned long)xTaskGetTickCount());
}

static void work(volatile uint32_t units)
{
    volatile uint32_t value = random_state;
    while(units-- != 0U) value = value * 1664525U + 1013904223U;
    random_state = value;
}

static void brake_task(void *arg)
{
    Command_t command; (void)arg;
    for(;;) if(xQueueReceive(brake_queue, &command, portMAX_DELAY) == pdPASS)
    {
        printf("EVT node=ECU-%u task=brake prio=8 seq=%lu kind=BRAKE state=EXECUTING rx_tick=%lu tick=%lu\n", NODE_ID, (unsigned long)command.sequence, (unsigned long)command.source_tick, (unsigned long)xTaskGetTickCount());
        work(9000000U);
        printf("EVT node=ECU-%u task=brake prio=8 seq=%lu kind=BRAKE state=DONE tick=%lu\n", NODE_ID, (unsigned long)command.sequence, (unsigned long)xTaskGetTickCount());
    }
}

static void normal_task(void *arg)
{
    Command_t command; (void)arg;
    for(;;) if(xQueueReceive(normal_queue, &command, portMAX_DELAY) == pdPASS)
    {
        printf("EVT node=ECU-%u task=normal prio=4 seq=%lu kind=%s state=EXECUTING tick=%lu\n", NODE_ID, (unsigned long)command.sequence, command.kind, (unsigned long)xTaskGetTickCount());
        work(3500000U + (random_value() % 1800000U));
        printf("EVT node=ECU-%u task=normal prio=4 seq=%lu kind=%s state=DONE tick=%lu\n", NODE_ID, (unsigned long)command.sequence, command.kind, (unsigned long)xTaskGetTickCount());
    }
}

static void sensor_task(void *arg)
{
    (void)arg;
    for(;;)
    {
        vTaskDelay(pdMS_TO_TICKS(40U + (random_value() % 360U)));
        event("sensor", 3U, "START");
        work(2200000U + (random_value() % 1200000U));
        event("sensor", 3U, "DONE");
    }
}

static void heartbeat_task(void *arg)
{
    (void)arg;
    for(;;)
    {
        event("heartbeat", 2U, "START");
        work(1200000U);
        event("heartbeat", 2U, "DONE");
        vTaskDelay(pdMS_TO_TICKS(250U));
    }
}

/* Continuous low-priority writer: no delay and no periodic wake-up. */
static void logger_task(void *arg)
{
    uint32_t report_count = 0U;
    (void)arg;
    for(;;)
    {
        if((report_count++ & 15U) == 0U) { event("logger", 1U, "RUN"); flush_switch_trace(); }
        work(80000U);
    }
}

#if defined(ROLE_ecu)
static void network_task(void *arg)
{
    Socket_t socket_handle = FreeRTOS_socket(FREERTOS_AF_INET, FREERTOS_SOCK_DGRAM, FREERTOS_IPPROTO_UDP);
    struct freertos_sockaddr address = {0}; Command_t command; TickType_t timeout = pdMS_TO_TICKS(1000U); (void)arg;
    configASSERT(socket_handle != FREERTOS_INVALID_SOCKET);
    address.sin_port = FreeRTOS_htons(UDP_PORT);
    configASSERT(FreeRTOS_bind(socket_handle, &address, sizeof(address)) == 0);
    (void)FreeRTOS_setsockopt(socket_handle, 0, FREERTOS_SO_RCVTIMEO, &timeout, sizeof(timeout));
    printf("EVT node=ECU-%u task=network prio=6 state=LISTENING group=%s port=%u tick=%lu\n", NODE_ID, GROUP_TEXT, UDP_PORT, (unsigned long)xTaskGetTickCount());
    for(;;) if(FreeRTOS_recvfrom(socket_handle, &command, sizeof(command), 0, NULL, NULL) == sizeof(command))
    {
        command.kind[7] = '\0';
        printf("EVT node=ECU-%u task=network prio=6 seq=%lu kind=%s state=RECEIVED rx_tick=%lu tick=%lu\n", NODE_ID, (unsigned long)command.sequence, command.kind, (unsigned long)command.source_tick, (unsigned long)xTaskGetTickCount());
        (void)xQueueSend(strcmp(command.kind, "BRAKE") == 0 ? brake_queue : normal_queue, &command, portMAX_DELAY);
        printf("EVT node=ECU-%u task=network prio=6 seq=%lu kind=%s state=QUEUED tick=%lu\n", NODE_ID, (unsigned long)command.sequence, command.kind, (unsigned long)xTaskGetTickCount());
    }
}
#else
static void network_task(void *arg)
{
    Socket_t socket_handle = FreeRTOS_socket(FREERTOS_AF_INET, FREERTOS_SOCK_DGRAM, FREERTOS_IPPROTO_UDP);
    struct freertos_sockaddr address = {0}; Command_t command; char line[8]; UBaseType_t length = 0U; uint32_t sequence = 0U; int ch; (void)arg;
    configASSERT(socket_handle != FREERTOS_INVALID_SOCKET);
    address.sin_port = FreeRTOS_htons(UDP_PORT);
    address.sin_address.ulIP_IPv4 = FreeRTOS_inet_addr_quick(239U,255U,0U,1U);
    printf("EVT node=CTRL-%u task=network prio=6 state=READY group=%s port=%u tick=%lu\n", NODE_ID, GROUP_TEXT, UDP_PORT, (unsigned long)xTaskGetTickCount());
    for(;;)
    {
        ch = uart_getc_nonblocking();
        if(ch < 0) { vTaskDelay(1U); continue; }
        if(ch == '\r' || ch == '\n')
        {
            if(length != 0U)
            {
                line[length] = '\0';
                if(strcmp(line,"BRAKE") == 0 || strcmp(line,"TURN") == 0 || strcmp(line,"NORMAL") == 0)
                {
                    command.sequence = ++sequence; command.source_tick = xTaskGetTickCount(); strncpy(command.kind,line,7U); command.kind[7] = '\0';
                    printf("EVT node=CTRL-%u task=network prio=6 seq=%lu kind=%s state=SENT tx_tick=%lu tick=%lu\n", NODE_ID, (unsigned long)command.sequence, command.kind, (unsigned long)command.source_tick, (unsigned long)xTaskGetTickCount());
                    (void)FreeRTOS_sendto(socket_handle, &command, sizeof(command), 0, &address, sizeof(address));
                }
                length = 0U;
            }
        }
        else if(length < 7U) line[length++] = (char)ch; else length = 0U;
    }
}
#endif

static void network_up(eIPCallbackEvent_t event_code)
{
    static BaseType_t created = pdFALSE;
    if(event_code == eNetworkUp && created == pdFALSE)
    {
        enable_multicast_receive();
        brake_queue = xQueueCreate(QUEUE_CAPACITY, sizeof(Command_t)); normal_queue = xQueueCreate(QUEUE_CAPACITY, sizeof(Command_t)); configASSERT(brake_queue && normal_queue);
        configASSERT(xTaskCreate(brake_task,"brake",configMINIMAL_STACK_SIZE,NULL,8U,NULL) == pdPASS);
        configASSERT(xTaskCreate(network_task,"network",configMINIMAL_STACK_SIZE,NULL,6U,NULL) == pdPASS);
#if defined(ROLE_ecu)
        configASSERT(xTaskCreate(normal_task,"normal",configMINIMAL_STACK_SIZE,NULL,4U,NULL) == pdPASS);
        configASSERT(xTaskCreate(sensor_task,"sensor",configMINIMAL_STACK_SIZE,NULL,3U,NULL) == pdPASS);
        configASSERT(xTaskCreate(heartbeat_task,"heartbeat",configMINIMAL_STACK_SIZE,NULL,2U,NULL) == pdPASS);
        configASSERT(xTaskCreate(logger_task,"logger",configMINIMAL_STACK_SIZE,NULL,1U,NULL) == pdPASS);
        printf("EVT node=ECU-%u state=TASKS_READY brake_prio=8 network_prio=6 normal_prio=4 sensor_prio=3 heartbeat_prio=2 logger_prio=1 queue_capacity=%u\n", NODE_ID, QUEUE_CAPACITY);
#else
        printf("EVT node=CTRL-%u state=TASKS_READY network_prio=6\n", NODE_ID);
#endif
        created = pdTRUE;
    }
}

void vApplicationIPNetworkEventHook(eIPCallbackEvent_t event_code) { network_up(event_code); }
BaseType_t xApplicationGetRandomNumber(uint32_t *number) { random_state = random_state * 1664525U + 1013904223U; *number = random_state; return pdTRUE; }
int main(void) { NVIC_SetPriority(ETHERNET_IRQn, configMAC_INTERRUPT_PRIORITY); configASSERT(FreeRTOS_IPInit(ip,mask,zero,zero,ucMACAddress) == pdTRUE); vTaskStartScheduler(); for(;;) {} }
void vApplicationMallocFailedHook(void) { for(;;) {} }
void vAssertCalled(void) { for(;;) {} }
