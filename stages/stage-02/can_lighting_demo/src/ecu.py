"""Virtual lighting ECU with five observable, independently triggered tasks."""
import argparse
import queue
from collections import OrderedDict, deque
import signal
import threading
import time

import can

from protocol import (ALL_LAMPS, COMMANDS, CONTROL_ID, SIM_ID, STATUS_ID,
                      decode_command, encode_status)
from runtime import emit_event, mono_ms


class LightingECU:
    def __init__(self, channel: str = "vcan0"):
        self.channel = channel
        self.bus = can.Bus(interface="socketcan", channel=channel)
        self.stop_event = threading.Event()
        self.rx_queue: queue.Queue = queue.Queue(maxsize=256)
        self.control_queue: queue.Queue = queue.Queue(maxsize=256)
        self.ack_queue = deque()
        self.command_cache = OrderedDict()
        self.lock = threading.RLock()
        self.trace_lock = threading.Lock()
        self.desired = 0
        self.faults = 0
        self.last_seq = 0
        self.last_command = 255
        self.last_valid_seq = 0
        self.has_valid_command = False
        self.last_result = 0
        self.brightness = 100
        self.rx_count = 0
        self.error_count = 0
        self.feedback_count = 0
        self.ignored_count = 0
        self.last_signature = None
        self.last_signature_ms = 0
        self.threads = []

    def start(self):
        specs = [
            ("CAN 命令接收", self.receiver_task),
            ("命令解析", self.parser_task),
            ("灯光控制", self.lighting_task),
            ("状态反馈", self.feedback_task),
            ("系统监控", self.monitor_task),
        ]
        for name, target in specs:
            thread = threading.Thread(name=name, target=target, daemon=True)
            thread.start()
            self.threads.append(thread)
        emit_event("ECU", "ecu_ready", "灯光 ECU 已上线", channel=self.channel,
                   control_id=f"0x{CONTROL_ID:03X}", status_id=f"0x{STATUS_ID:03X}")
        print(f"ECU_READY channel={self.channel} control=0x{CONTROL_ID:03X} status=0x{STATUS_ID:03X}", flush=True)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=0.4)
        self.bus.shutdown()
        emit_event("ECU", "ecu_stopped", "灯光 ECU 已停止")
        print("ECU_STOPPED", flush=True)

    def receiver_task(self):
        """事件驱动：SocketCAN 收帧唤醒；10ms 为接收等待/巡视上限。"""
        while not self.stop_event.is_set():
            message = self.bus.recv(timeout=0.010)
            if message is None:
                continue
            if message.arbitration_id not in (CONTROL_ID, SIM_ID):
                self.ignored_count += 1
                continue
            with self.lock:
                self.rx_count += 1
            if self.rx_queue.full():
                with self.lock:
                    self.error_count += 1
                emit_event("ECU", "queue_overflow", "接收缓冲区已满，丢弃报文", can_id=message.arbitration_id)
                continue
            payload = bytes(message.data)
            with self.trace_lock:
                try:
                    self.rx_queue.put_nowait(message)
                except queue.Full:
                    with self.lock:
                        self.error_count += 1
                    emit_event("ECU", "queue_overflow", "接收缓冲区已满，丢弃报文", can_id=message.arbitration_id)
                    continue
                emit_event("ECU", "can_rx", "CAN 报文到达 ECU", task="CAN 命令接收",
                           can_id=message.arbitration_id, dlc=message.dlc,
                           data=payload.hex(" ").upper(),
                           sequence=payload[1] if message.arbitration_id == CONTROL_ID and len(payload) > 1 else None)
                emit_event("ECU", "message_buffered", "报文已写入命令缓冲区",
                           task="CAN 命令接收", queue_depth=self.rx_queue.qsize(),
                           can_id=message.arbitration_id,
                           sequence=payload[1] if message.arbitration_id == CONTROL_ID and len(payload) > 1 else None)

    def parser_task(self):
        """事件驱动：阻塞等待接收队列中的 CAN 帧。"""
        while not self.stop_event.is_set():
            try:
                message = self.rx_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if message.arbitration_id == SIM_ID:
                if message.dlc != 8 or any(message.data[1:]) or (message.data[0] & ~ALL_LAMPS):
                    with self.lock:
                        self.error_count += 1
                    emit_event("ECU", "sim_frame_rejected", "仿真维护帧格式错误")
                    continue
                with self.trace_lock:
                    try:
                        self.control_queue.put_nowait(("fault", message.data[0], mono_ms()))
                        emit_event("ECU", "fault_frame_parsed", "已解析测试故障注入帧", fault_mask=message.data[0])
                        emit_event("ECU", "command_queued", "故障测试状态已排入灯光控制队列",
                                   task="命令解析", queue_depth=self.control_queue.qsize())
                    except queue.Full:
                        with self.lock:
                            self.error_count += 1
                        emit_event("ECU", "queue_overflow", "灯光控制队列已满", can_id=SIM_ID)
                continue
            command, error = decode_command(bytes(message.data))
            sequence = message.data[1] if message.dlc > 1 else 0
            code = message.data[0] if message.dlc > 0 else 255
            with self.trace_lock:
                if error:
                    try:
                        self.control_queue.put_nowait(("reject", (sequence, code, error), mono_ms()))
                        emit_event("ECU", "command_rejected", "命令校验未通过", task="命令解析",
                                   sequence=sequence, command=code, reason=error)
                        emit_event("ECU", "command_queued", "拒绝结果已排入灯光控制队列",
                                   task="命令解析", sequence=sequence,
                                   queue_depth=self.control_queue.qsize())
                    except queue.Full:
                        with self.lock:
                            self.error_count += 1
                        emit_event("ECU", "queue_overflow", "灯光控制队列已满", sequence=sequence)
                    continue
                emit_event("ECU", "command_parsed", "命令合法，已转换为内部控制指令",
                           task="命令解析", sequence=command.sequence, command=command.code,
                           command_name=COMMANDS[command.code], brightness=command.brightness)
                try:
                    self.control_queue.put_nowait(("command", command, mono_ms()))
                    emit_event("ECU", "command_queued", "内部灯光控制指令已排入消息队列",
                               task="命令解析", sequence=command.sequence,
                               queue_depth=self.control_queue.qsize())
                except queue.Full:
                    with self.lock:
                        self.error_count += 1
                    emit_event("ECU", "queue_overflow", "灯光控制队列已满", sequence=command.sequence)

    def _queue_ack_locked(self, sequence, command, result):
        payload = encode_status(self.desired, self.desired & ~self.faults,
                                sequence, command, result, self.faults,
                                self.brightness, self.rx_count)
        self.ack_queue.append(payload)

    def lighting_task(self):
        """事件驱动：内部控制队列唤醒；5ms 为任务轮询/响应预算。"""
        while not self.stop_event.is_set():
            try:
                kind, value, queued_ms = self.control_queue.get(timeout=0.005)
            except queue.Empty:
                continue
            with self.trace_lock:
                self._apply_control_item(kind, value, queued_ms)

    def _apply_control_item(self, kind, value, queued_ms):
        if kind == "fault":
            with self.lock:
                self.faults = value
                self.last_result = 2 if (self.desired & self.faults) else 0
                actual = self.desired & ~self.faults
                emit_event("ECU", "fault_changed", "虚拟灯具故障状态已更新",
                           task="灯光控制", fault_mask=value, actual_mask=actual)
            return
        if kind == "reject":
            sequence, code, reason = value
            with self.lock:
                self.error_count += 1
                self.last_seq = sequence
                self.last_command = code
                self.last_result = 1
                emit_event("ECU", "command_rejected_at_control", "非法命令未执行并已回报",
                           task="灯光控制", sequence=sequence, command=code, reason=reason)
                self._queue_ack_locked(sequence, code, 1)
            return

        command = value
        signature = (command.sequence, command.code, command.brightness)
        now = mono_ms()
        with self.lock:
            cached = self.command_cache.get(command.sequence)
            if cached and cached[0] == signature:
                emit_event("ECU", "duplicate_ignored", "重试报文已识别，防止切换命令重复执行",
                           task="灯光控制", sequence=command.sequence, command=command.code)
                self._queue_ack_locked(command.sequence, command.code, cached[1])
                return
            if self.has_valid_command:
                delta = (command.sequence - self.last_valid_seq) & 0xFF
                if delta == 0 or delta >= 128:
                    self.error_count += 1
                    emit_event("ECU", "command_rejected", "过期/冲突序号被拒绝，避免旧命令覆盖新状态",
                               task="灯光控制", sequence=command.sequence,
                               command=command.code, reason="stale sequence")
                    self._queue_ack_locked(command.sequence, command.code, 1)
                    return
            before = self.desired
            if command.code == 0:
                self.desired = 0
            elif command.code == 1:
                self.desired = ALL_LAMPS
            elif command.code == 2:
                self.desired = (self.desired | 0b000001) & ~0b000010 & ~0b000100
            elif command.code == 3:
                self.desired = (self.desired | 0b000010) & ~0b000001 & ~0b000100
            elif command.code == 4:
                if self.desired & 0b000100:
                    self.desired &= ~0b000111
                else:
                    self.desired |= 0b000111
            elif command.code == 5:
                self.desired ^= 0b001000
            elif command.code == 6:
                self.desired ^= 0b010000
            elif command.code == 7:
                self.desired ^= 0b100000
            self.last_seq = command.sequence
            self.last_command = command.code
            self.last_valid_seq = command.sequence
            self.has_valid_command = True
            self.brightness = command.brightness
            self.last_result = 2 if (self.desired & self.faults) else 0
            after = self.desired
            actual = after & ~self.faults
            result = self.last_result
            self.command_cache[command.sequence] = (signature, result)
            self.command_cache.move_to_end(command.sequence)
            while len(self.command_cache) > 64:
                self.command_cache.popitem(last=False)
            emit_event("ECU", "lamp_applied", "灯光控制任务已更新执行状态",
                       task="灯光控制", sequence=command.sequence, command=command.code,
                       command_name=COMMANDS[command.code], before_mask=before,
                       desired_mask=after, actual_mask=actual,
                       fault_mask=self.faults, result=result,
                       queue_wait_ms=max(0, now - queued_ms))
            self._queue_ack_locked(command.sequence, command.code, result)

    def actual_mask(self):
        with self.lock:
            return self.desired & ~self.faults

    def feedback_task(self):
        """定时任务：每 20ms 发送一帧完整状态快照。"""
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            deadline += 0.020
            wait = deadline - time.monotonic()
            if wait > 0 and self.stop_event.wait(wait):
                break
            with self.lock:
                desired, faults = self.desired, self.faults
                actual = desired & ~faults
                sequence, command = self.last_seq, self.last_command
                result = 1 if self.last_result == 1 else (2 if (desired & faults) else self.last_result)
                brightness, rx_count = self.brightness, self.rx_count
                payload = self.ack_queue.popleft() if self.ack_queue else encode_status(
                    desired, actual, sequence, command, result, faults, brightness, rx_count)
            try:
                self.bus.send(can.Message(arbitration_id=STATUS_ID, data=payload,
                                          is_extended_id=False), timeout=0.02)
                self.feedback_count += 1
            except can.CanError as exc:
                with self.lock:
                    self.error_count += 1
                emit_event("ECU", "feedback_send_error", "状态反馈发送失败，准备重试",
                           task="状态反馈", detail=str(exc))
                try:
                    self.bus.send(can.Message(arbitration_id=STATUS_ID, data=payload,
                                              is_extended_id=False), timeout=0.02)
                    self.feedback_count += 1
                    emit_event("ECU", "feedback_retry_success", "状态反馈重试成功",
                               task="状态反馈")
                except can.CanError as retry_exc:
                    with self.lock:
                        self.error_count += 1
                    emit_event("ECU", "feedback_retry_failed", "状态反馈重试仍失败",
                               task="状态反馈", detail=str(retry_exc))

    def monitor_task(self):
        """定时任务：每 100ms 汇总队列、收发和虚拟执行器健康状态。"""
        deadline = time.monotonic()
        sample = 0
        while not self.stop_event.is_set():
            deadline += 0.100
            wait = deadline - time.monotonic()
            if wait > 0 and self.stop_event.wait(wait):
                break
            sample += 1
            with self.lock:
                desired, faults = self.desired, self.faults
                rx_count, errors = self.rx_count, self.error_count
                result = 1 if self.last_result == 1 else (2 if (desired & faults) else self.last_result)
            emit_event("ECU", "system_monitor", "系统监控采样",
                       task="系统监控", rx_count=rx_count, error_count=errors,
                       rx_queue=self.rx_queue.qsize(), control_queue=self.control_queue.qsize(),
                       desired_mask=desired, actual_mask=desired & ~faults,
                       fault_mask=faults, result=result,
                       feedback_count=self.feedback_count)


def main():
    parser = argparse.ArgumentParser(description="SocketCAN virtual car-light ECU")
    parser.add_argument("--channel", default="vcan0")
    args = parser.parse_args()
    ecu = LightingECU(args.channel)
    ecu.start()
    signal.signal(signal.SIGTERM, lambda *_: ecu.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: ecu.stop_event.set())
    try:
        while not ecu.stop_event.wait(0.25):
            pass
    finally:
        ecu.stop()


if __name__ == "__main__":
    main()
