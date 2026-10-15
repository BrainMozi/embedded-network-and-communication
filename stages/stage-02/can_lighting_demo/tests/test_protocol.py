import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from protocol import (ALL_LAMPS, COMMANDS, decode_command, decode_status,
                      encode_command, encode_fault, encode_status, mask_to_states)


class ProtocolTests(unittest.TestCase):
    def test_all_defined_commands_round_trip(self):
        for code in COMMANDS:
            with self.subTest(code=code):
                cmd, error = decode_command(encode_command(code, 173, 72))
                self.assertIsNone(error)
                self.assertEqual((cmd.code, cmd.sequence, cmd.brightness), (code, 173, 72))

    def test_rejects_unknown_code_and_bad_payload(self):
        cmd, error = decode_command(bytes((254, 3, 100, 0, 0, 0, 0, 0)))
        self.assertIsNone(cmd)
        self.assertIn("未定义", error)
        self.assertIsNotNone(decode_command(b"\x00\x01")[1])
        self.assertIsNotNone(decode_command(bytes((0, 1, 101, 0, 0, 0, 0, 0)))[1])
        self.assertIsNotNone(decode_command(bytes((0, 1, 100, 1, 0, 0, 0, 0)))[1])

    def test_status_frame_is_exactly_eight_bytes(self):
        payload = encode_status(ALL_LAMPS, 0b101011, 41, 5, 2, 0b000100, 80, 217)
        self.assertEqual(len(payload), 8)
        status = decode_status(payload)
        self.assertEqual(status["sequence"], 41)
        self.assertEqual(status["brightness"], 80)
        self.assertEqual(status["actual_mask"], 0b101011)
        self.assertEqual(status["fault_mask"], 0b000100)

    def test_six_lamp_mask(self):
        self.assertEqual(ALL_LAMPS, 0x3F)
        states = mask_to_states(0b000101)
        self.assertTrue(states["左转向"])
        self.assertTrue(states["双闪"])
        self.assertFalse(states["尾灯"])
        self.assertEqual(len(encode_fault(ALL_LAMPS)), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
