#!/usr/bin/env python3
"""Exercise the live HTTP↔CAN↔ECU feedback loop; run via VS Code task first."""
import json
import time
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8765"


def get(path):
    with urlopen(BASE + path, timeout=2) as response:
        return json.loads(response.read())


def post(path, data):
    body = json.dumps(data).encode()
    req = Request(BASE + path, data=body,
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=2) as response:
        return json.loads(response.read())


def wait_for(predicate, label, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = get("/api/state")
        if predicate(state):
            print("PASS", label)
            return state
        time.sleep(0.04)
    raise SystemExit(f"FAIL: timeout waiting for {label}; last state={state}")


def full_chain(state, sequence):
    required = ["controller_command_sent", "can_rx", "message_buffered",
                "command_parsed", "command_queued", "lamp_applied", "feedback_confirmed"]
    events = list(reversed([e for e in state["events"] if e.get("sequence") == sequence]))
    found = [e["kind"] for e in events]
    cursor = 0
    for kind in required:
        try:
            cursor = found.index(kind, cursor) + 1
        except ValueError:
            return False
    return True


print("health:", get("/healthz"))
with urlopen(BASE + "/", timeout=2) as response:
    page = response.read().decode("utf-8")
    assert "端到端通信闭环" in page and "CAN 实时报文" in page
    print("PASS live dashboard page served")
reset = post("/api/reset", {})
wait_for(lambda s: s["history"][0]["sequence"] == reset["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认"
         and s["status"]["actual_mask"] == 0, "clear lights with application confirmation")
all_on = post("/api/command", {"code": 1, "brightness": 65})
wait_for(lambda s: s["status"]["desired_mask"] == 0x3F
         and s["status"]["brightness"] == 65
         and s["history"][0]["sequence"] == all_on["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认", "all-on and brightness parameter")
left = post("/api/command", {"code": 2, "brightness": 100})
wait_for(lambda s: s["actual_lamps"]["左转向"] is True
         and s["actual_lamps"]["右转向"] is False
         and s["actual_lamps"]["双闪"] is False
         and s["history"][0]["sequence"] == left["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认"
         and full_chain(s, left["sequence"]),
         "left-turn full end-to-end event chain")
right = post("/api/command", {"code": 3, "brightness": 100})
wait_for(lambda s: s["actual_lamps"]["右转向"] is True
         and s["actual_lamps"]["左转向"] is False
         and s["history"][0]["sequence"] == right["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认", "right-turn interlock")
hazard = post("/api/command", {"code": 4, "brightness": 100})
wait_for(lambda s: s["actual_lamps"]["双闪"] is True
         and s["actual_lamps"]["左转向"] is True
         and s["actual_lamps"]["右转向"] is True
         and s["history"][0]["sequence"] == hazard["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认", "hazard priority")
hazard_off = post("/api/command", {"code": 4, "brightness": 100})
wait_for(lambda s: s["actual_lamps"]["双闪"] is False
         and s["actual_lamps"]["左转向"] is False
         and s["actual_lamps"]["右转向"] is False
         and s["history"][0]["sequence"] == hazard_off["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认", "hazard toggle-off")
for code, lamp, expected, label in ((5, "刹车灯", False, "brake lamp toggle-off"),
                                    (6, "前大灯", False, "headlight toggle-off"),
                                    (7, "尾灯", False, "tail lamp toggle-off"),
                                    (5, "刹车灯", True, "brake lamp toggle-on"),
                                    (6, "前大灯", True, "headlight toggle-on"),
                                    (7, "尾灯", True, "tail lamp toggle-on")):
    req = post("/api/command", {"code": code, "brightness": 100})
    wait_for(lambda s, lamp=lamp, expected=expected, req=req: s["actual_lamps"][lamp] is expected
             and s["history"][0]["sequence"] == req["sequence"]
             and s["history"][0]["status"] == "状态反馈已确认", label)
all_off = post("/api/command", {"code": 0, "brightness": 100})
wait_for(lambda s: s["status"]["actual_mask"] == 0
         and s["history"][0]["sequence"] == all_off["sequence"]
         and s["history"][0]["status"] == "状态反馈已确认", "all-off")
post("/api/fault", {"mask": 1 << 3})
post("/api/command", {"code": 5, "brightness": 100})
wait_for(lambda s: s["status"]["result"] == 2
         and s["fault_lamps"]["刹车灯"] is True
         and s["actual_lamps"]["刹车灯"] is False, "virtual brake-lamp fault surfaced in status frame")
invalid = post("/api/invalid", {})
wait_for(lambda s: s["history"][0]["sequence"] == invalid["sequence"]
         and s["history"][0]["status"] == "命令已拒绝：校验失败", "invalid command rejected")
reset = post("/api/reset", {})
final = wait_for(lambda s: s["history"][0]["sequence"] == reset["sequence"]
                 and s["history"][0]["status"] == "状态反馈已确认"
                 and s["status"]["actual_mask"] == 0
                 and s["status"]["fault_mask"] == 0, "reset to safe all-off state")
print("RESULT", json.dumps({"commands": final["stats"]["commands_sent"],
                           "confirmed": final["stats"]["confirmed"],
                           "rejected": final["stats"]["rejected"],
                           "faulted": final["stats"]["faulted"],
                           "feedback_frames": final["stats"]["feedback_frames"],
                           "avg_latency_ms": final["stats"]["avg_latency_ms"],
                           "retries": final["stats"]["retries"],
                           "timeouts": final["stats"]["timeouts"],
                           "errors": final["stats"]["errors"]}, ensure_ascii=False))
