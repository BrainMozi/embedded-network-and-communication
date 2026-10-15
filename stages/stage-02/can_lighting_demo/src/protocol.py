"""Course CAN protocol for the virtual distributed lighting ECU."""
from dataclasses import dataclass

CONTROL_ID = 0x256
STATUS_ID = 0x257
SIM_ID = 0x258  # Test-only fault injection; not a vehicle control frame.
DLC = 8

COMMANDS = {
    0: "全关", 1: "全开", 2: "左转向", 3: "右转向",
    4: "双闪切换", 5: "刹车灯切换", 6: "前大灯切换", 7: "尾灯切换",
}
LAMP_NAMES = ("左转向", "右转向", "双闪", "刹车灯", "前大灯", "尾灯")
ALL_LAMPS = (1 << len(LAMP_NAMES)) - 1


@dataclass(frozen=True)
class Command:
    code: int
    sequence: int
    brightness: int = 100


def encode_command(code: int, sequence: int, brightness: int = 100) -> bytes:
    if not 0 <= code <= 255 or not 0 <= sequence <= 255 or not 0 <= brightness <= 100:
        raise ValueError("command fields are outside protocol range")
    return bytes((code, sequence, brightness, 0, 0, 0, 0, 0))


def decode_command(data: bytes) -> tuple[Command | None, str | None]:
    if len(data) != DLC:
        return None, f"DLC 必须为 {DLC}，实际为 {len(data)}"
    code, sequence, brightness = data[:3]
    if code not in COMMANDS:
        return None, f"未定义命令码 0x{code:02X}"
    if brightness > 100:
        return None, f"亮度参数超范围：{brightness}"
    if any(data[3:]):
        return None, "保留字段必须为 0"
    return Command(code, sequence, brightness), None


def encode_fault(mask: int) -> bytes:
    if mask & ~ALL_LAMPS:
        raise ValueError("fault mask contains unknown lamp bits")
    return bytes((mask, 0, 0, 0, 0, 0, 0, 0))


def encode_status(desired: int, actual: int, sequence: int, command: int,
                  result: int, faults: int, brightness: int, rx_count: int) -> bytes:
    return bytes((desired & 0x3F, actual & 0x3F, sequence & 0xFF,
                  command & 0xFF, result & 0xFF, faults & 0x3F,
                  max(0, min(100, brightness)), rx_count & 0xFF))


def decode_status(data: bytes) -> dict:
    if len(data) != DLC:
        raise ValueError(f"status DLC must be {DLC}, got {len(data)}")
    desired, actual, sequence, command, result, faults, brightness, rx_count = data
    return {
        "desired_mask": desired & 0x3F,
        "actual_mask": actual & 0x3F,
        "sequence": sequence,
        "command": command,
        "result": result,
        "fault_mask": faults & 0x3F,
        "brightness": brightness,
        "rx_count": rx_count,
    }


def mask_to_states(mask: int) -> dict[str, bool]:
    return {name: bool(mask & (1 << bit)) for bit, name in enumerate(LAMP_NAMES)}
