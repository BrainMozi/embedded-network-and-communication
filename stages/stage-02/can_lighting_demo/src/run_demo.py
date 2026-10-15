"""Start one ECU process and the local control/visualization server."""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if subprocess.run(["ip", "link", "show", "vcan0"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        print("vcan0 不存在。先运行：sudo bash scripts/setup_vcan.sh", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = ROOT / "data" / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CANDEMO_EVENT_LOG"] = str(run_dir / "events.jsonl")
    os.environ["CANDEMO_EVENT_LOG"] = env["CANDEMO_EVENT_LOG"]
    from dashboard import serve
    ecu_log = (run_dir / "ecu.log").open("ab", buffering=0)
    ecu = subprocess.Popen([sys.executable, str(ROOT / "src" / "ecu.py"),
                            "--channel", "vcan0"], cwd=ROOT, env=env,
                           stdout=ecu_log, stderr=subprocess.STDOUT,
                           start_new_session=True)
    print(f"实测数据目录：{run_dir}")
    print(f"ECU 日志：{run_dir / 'ecu.log'}")
    time.sleep(0.45)
    if ecu.poll() is not None:
        ecu_log.close()
        print("ECU 未能启动；检查 vcan0 与 Python 环境。日志：", run_dir / "ecu.log",
              file=sys.stderr)
        return 1
    def graceful_stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, graceful_stop)
    try:
        has_desktop = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        serve(host="127.0.0.1", port=8765, channel="vcan0", open_browser=has_desktop)
    finally:
        if ecu.poll() is None:
            ecu.send_signal(signal.SIGTERM)
            try:
                ecu.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ecu.kill()
                ecu.wait(timeout=2)
        ecu_log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
