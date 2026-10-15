"""Local control desk: sends real CAN commands and correlates ECU feedback."""
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import can

from protocol import (COMMANDS, CONTROL_ID, LAMP_NAMES, SIM_ID, STATUS_ID,
                      decode_status, encode_command, encode_fault, mask_to_states)
from runtime import EVENT_LOG, emit_event, mono_ms

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
TASK_SPEC = [
    {"name": "CAN 命令接收", "period": "10 ms", "trigger": "事件驱动"},
    {"name": "命令解析", "period": "事件驱动", "trigger": "消息队列"},
    {"name": "灯光控制", "period": "5 ms", "trigger": "事件驱动"},
    {"name": "状态反馈", "period": "20 ms", "trigger": "定时触发"},
    {"name": "系统监控", "period": "100 ms", "trigger": "定时触发"},
]


class ControlPlane:
    def __init__(self, channel="vcan0"):
        self.channel = channel
        self.bus = can.Bus(interface="socketcan", channel=channel)
        self.bus_lock = threading.Lock()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.next_sequence = int(time.time() * 1000) & 0xFF
        self.pending = {}
        self.history = deque(maxlen=30)
        self.frames = deque(maxlen=160)
        self.command_frames = deque(maxlen=24)
        self.events = deque(maxlen=400)
        self.latencies = deque(maxlen=100)
        self.status = {"desired_mask": 0, "actual_mask": 0, "sequence": 0,
                       "command": 255, "result": 0, "fault_mask": 0,
                       "brightness": 100, "rx_count": 0}
        self.stats = {"commands_sent": 0, "confirmed": 0, "rejected": 0,
                      "faulted": 0, "timeouts": 0, "retries": 0,
                      "feedback_frames": 0, "errors": 0}
        self.last_task_ms = {}
        self.health = {"rx_count": 0, "error_count": 0,
                       "rx_queue": 0, "control_queue": 0,
                       "feedback_count": 0, "sample_ms": None}
        self.event_offset = 0
        self.threads = []

    def start(self):
        for name, target in (("CAN feedback monitor", self.feedback_reader),
                             ("CAN command retry", self.retry_worker),
                             ("event log reader", self.event_reader)):
            t = threading.Thread(name=name, target=target, daemon=True)
            t.start()
            self.threads.append(t)
        emit_event("CTRL", "controller_ready", "驾驶员控制台已连接 CAN 总线",
                   channel=self.channel, control_id=f"0x{CONTROL_ID:03X}",
                   status_id=f"0x{STATUS_ID:03X}")

    def close(self):
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=0.5)
        self.bus.shutdown()

    def add_event(self, event):
        with self.lock:
            task = event.get("task")
            if task:
                self.last_task_ms[task] = event.get("ms", mono_ms())
            if event.get("kind") == "system_monitor":
                self.health.update({k: event.get(k, self.health.get(k))
                                    for k in ("rx_count", "error_count", "rx_queue",
                                              "control_queue", "feedback_count")})
                self.health["sample_ms"] = event.get("ms", mono_ms())
            else:
                self.events.append(event)

    def send_command(self, code, brightness=100, invalid=False):
        with self.lock:
            self.next_sequence = (self.next_sequence + 1) & 0xFF
            seq = self.next_sequence
        actual_code = code if not invalid else 0xFE
        payload = encode_command(actual_code, seq, brightness)
        now = mono_ms()
        req = {"sequence": seq, "command": actual_code,
               "label": COMMANDS.get(actual_code, "非法命令测试"),
               "brightness": brightness, "first_ms": now,
               "last_send_ms": now, "attempts": 1, "status": "等待 ECU 回报"}
        with self.lock:
            self.pending[seq] = req
            self.history.appendleft(dict(req))
            self.stats["commands_sent"] += 1
        emit_event("CTRL", "controller_command_sent", "控制端提交 0x256 CAN 命令帧",
                   can_id=CONTROL_ID, sequence=seq, command=actual_code,
                   command_name=req["label"], data=payload.hex(" ").upper())
        try:
            with self.bus_lock:
                self.bus.send(can.Message(arbitration_id=CONTROL_ID, data=payload,
                                          is_extended_id=False), timeout=0.2)
            with self.lock:
                frame = {"ms": now, "id": CONTROL_ID, "dlc": len(payload),
                         "data": payload.hex(" ").upper(),
                         "sequence": seq, "command": actual_code, "direction": "TX"}
                self.frames.appendleft(frame)
                self.command_frames.appendleft(frame)
        except can.CanError as exc:
            with self.lock:
                self.pending.pop(seq, None)
                self.stats["errors"] += 1
                self._update_history(seq, "发送失败")
            emit_event("CTRL", "controller_send_error", "CAN 命令发送失败", sequence=seq,
                       detail=str(exc))
            raise
        return req

    def inject_fault(self, mask):
        payload = encode_fault(mask)
        emit_event("CTRL", "fault_injected", "控制端提交仿真故障注入帧（仅测试用途）",
                   can_id=SIM_ID, fault_mask=mask, data=payload.hex(" ").upper())
        with self.bus_lock:
            self.bus.send(can.Message(arbitration_id=SIM_ID, data=payload,
                                      is_extended_id=False), timeout=0.2)
        with self.lock:
            frame = {"ms": mono_ms(), "id": SIM_ID, "dlc": len(payload),
                     "data": payload.hex(" ").upper(), "direction": "TX"}
            self.frames.appendleft(frame)
            self.command_frames.appendleft(frame)


    def _update_history(self, seq, label):
        for item in self.history:
            if item["sequence"] == seq:
                item["status"] = label
                return

    def feedback_reader(self):
        while not self.stop_event.is_set():
            try:
                message = self.bus.recv(timeout=0.25)
            except can.CanError as exc:
                with self.lock:
                    self.stats["errors"] += 1
                emit_event("CTRL", "feedback_read_error", "CAN 状态接收失败", detail=str(exc))
                time.sleep(0.25)
                continue
            if message is None or message.arbitration_id != STATUS_ID:
                continue
            try:
                status = decode_status(bytes(message.data))
            except ValueError:
                with self.lock:
                    self.stats["errors"] += 1
                continue
            now = mono_ms()
            frame = {"ms": now, "id": STATUS_ID, "dlc": message.dlc,
                     "data": bytes(message.data).hex(" ").upper(), **status}
            with self.lock:
                self.status = status
                frame["direction"] = "RX"
                self.frames.appendleft(frame)
                self.last_task_ms["状态反馈"] = now
                self.stats["feedback_frames"] += 1
                req = self.pending.get(status["sequence"])
                if req and status["command"] == req["command"]:
                    if status["result"] == 1:
                        label = "命令已拒绝：校验失败"
                        self.stats["rejected"] += 1
                    elif status["result"] == 2:
                        label = "命令已执行：检测到灯路故障"
                        self.stats["faulted"] += 1
                    elif status["result"] == 0:
                        label = "状态反馈已确认"
                        self.stats["confirmed"] += 1
                    else:
                        label = "收到未知状态"
                    latency = max(0, now - req["first_ms"])
                    self.latencies.append(latency)
                    self._update_history(req["sequence"], label)
                    self.pending.pop(status["sequence"], None)
                    self.last_task_ms["状态反馈"] = now
                    emit_event("CTRL", "feedback_confirmed" if status["result"] == 0 else "feedback_exception",
                               label, task="状态反馈", sequence=status["sequence"], command=status["command"],
                               result=status["result"], desired_mask=status["desired_mask"],
                               actual_mask=status["actual_mask"], fault_mask=status["fault_mask"],
                               latency_ms=latency)

    def retry_worker(self):
        while not self.stop_event.wait(0.05):
            now = mono_ms()
            expired = []
            retries = []
            with self.lock:
                for seq, req in list(self.pending.items()):
                    if now - req["last_send_ms"] < 300:
                        continue
                    if req["attempts"] < 3:
                        req["attempts"] += 1
                        req["last_send_ms"] = now
                        req["status"] = f"重试 {req['attempts'] - 1}/2"
                        self.stats["retries"] += 1
                        self._update_history(seq, req["status"])
                        retries.append((seq, req.copy()))
                    else:
                        self.pending.pop(seq, None)
                        self.stats["timeouts"] += 1
                        self._update_history(seq, "超时：未收到状态反馈")
                        expired.append(seq)
            for seq, req in retries:
                try:
                    payload = encode_command(req["command"], seq, req["brightness"])
                    emit_event("CTRL", "controller_retry", "控制端提交同序号重试帧",
                               sequence=seq, attempt=req["attempts"])
                    with self.bus_lock:
                        self.bus.send(can.Message(arbitration_id=CONTROL_ID, data=payload,
                                                  is_extended_id=False), timeout=0.2)
                    with self.lock:
                        frame = {"ms": now, "id": CONTROL_ID, "dlc": len(payload),
                                 "data": payload.hex(" ").upper(),
                                 "sequence": seq, "command": req["command"], "direction": "TX"}
                        self.frames.appendleft(frame)
                        self.command_frames.appendleft(frame)
                except can.CanError as exc:
                    with self.lock:
                        self.stats["errors"] += 1
                    emit_event("CTRL", "retry_error", "CAN 重试发送失败", sequence=seq,
                               detail=str(exc))
            for seq in expired:
                emit_event("CTRL", "controller_timeout", "等待 ECU 状态反馈超时", sequence=seq)

    def event_reader(self):
        while not self.stop_event.is_set():
            try:
                if EVENT_LOG.exists():
                    size = EVENT_LOG.stat().st_size
                    if self.event_offset > size:
                        self.event_offset = 0
                    with EVENT_LOG.open("rb") as f:
                        f.seek(self.event_offset)
                        chunk = f.read()
                        self.event_offset = f.tell()
                    for raw in chunk.splitlines():
                        if not raw:
                            continue
                        try:
                            self.add_event(json.loads(raw.decode("utf-8")))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
            except OSError:
                pass
            self.stop_event.wait(0.05)

    def snapshot(self):
        with self.lock:
            status = dict(self.status)
            desired = status["desired_mask"]
            actual = status["actual_mask"]
            faults = status["fault_mask"]
            stats = dict(self.stats)
            lat = list(self.latencies)
            stats["avg_latency_ms"] = round(sum(lat) / len(lat), 1) if lat else None
            stats["max_latency_ms"] = max(lat) if lat else None
            stats["pending"] = len(self.pending)
            health = dict(self.health)
            task_map = dict(self.last_task_ms)
            now = mono_ms()
            ecu_online = (self.last_task_ms.get("状态反馈") is not None and now - self.last_task_ms["状态反馈"] < 1000)
            tasks = []
            task_names = ("CAN 命令接收", "命令解析", "灯光控制", "状态反馈", "系统监控")
            spec = {x["name"]: x for x in TASK_SPEC}
            for name in task_names:
                last = task_map.get(name)
                tasks.append({**spec[name], "online": ecu_online,
                              "last_ms": last})
            events = list(self.events)
            return {
                "ecu_online": ecu_online,
                "connected": True, "channel": self.channel, "status": status,
                "desired_lamps": mask_to_states(desired),
                "actual_lamps": mask_to_states(actual),
                "fault_lamps": mask_to_states(faults),
                "stats": stats, "health": health, "tasks": tasks,
                "events": list(events)[-24:][::-1],
                "frames": sorted(list(self.command_frames)[:6] +
                                 [f for f in self.frames if f["id"] == STATUS_ID][:8],
                                 key=lambda f: f["ms"], reverse=True),
                "history": list(self.history)[:8],
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "CANLightDesk/1.0"
    plane: ControlPlane

    def log_message(self, *_args):
        return

    def _send(self, code, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            try:
                return self._send(200, self.plane.snapshot())
            except Exception as exc:
                return self._send(500, {"error": f"state snapshot failed: {exc}"})
        if path == "/healthz":
            return self._send(200, {"ok": True, "channel": self.plane.channel})
        if path == "/" or path == "/index.html":
            try:
                return self._send(200, (WEB_ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            except OSError as exc:
                return self._send(500, {"error": str(exc)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._body()
            path = urlparse(self.path).path
            if path == "/api/command":
                code = int(body.get("code", -1))
                brightness = int(body.get("brightness", 100))
                if code not in COMMANDS or not 0 <= brightness <= 100:
                    return self._send(400, {"error": "命令码或亮度参数无效"})
                return self._send(200, {"ok": True, **self.plane.send_command(code, brightness)})
            if path == "/api/invalid":
                return self._send(200, {"ok": True, **self.plane.send_command(0xFE, 100, invalid=True)})
            if path == "/api/fault":
                mask = int(body.get("mask", 0))
                if mask < 0 or mask > 0x3F:
                    return self._send(400, {"error": "fault mask must be 0..63"})
                self.plane.inject_fault(mask)
                return self._send(200, {"ok": True, "mask": mask})
            if path == "/api/reset":
                self.plane.inject_fault(0)
                return self._send(200, {"ok": True, **self.plane.send_command(0, 100)})
            return self._send(404, {"error": "not found"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": str(exc)})
        except can.CanError as exc:
            return self._send(503, {"error": f"CAN 发送失败：{exc}"})


def serve(host="127.0.0.1", port=8765, channel="vcan0", open_browser=False):
    plane = ControlPlane(channel)
    Handler.plane = plane
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    plane.start()
    emit_event("CTRL", "dashboard_ready", "CAN 闭环可视化面板已就绪",
               url=f"http://{host}:{port}")
    print(f"DASHBOARD_READY http://{host}:{port} channel={channel}", flush=True)
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(f"http://{host}:{port}")
        except Exception as exc:
            print(f"浏览器未能自动打开：{exc}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        plane.close()
        server.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--channel", default="vcan0")
    args = parser.parse_args()
    serve(args.host, args.port, args.channel)
