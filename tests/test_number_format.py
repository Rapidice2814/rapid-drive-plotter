"""Displayed decimals retain wire precision without exponent notation."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "USB_debugger"))
from ui.number_format import decimal_text


class NumberFormatTests(unittest.TestCase):
    def test_values_use_plain_decimal_notation(self):
        for value, expected in ((80, "80"), (80.0, "80"), (1000000.0, "1000000"),
                                (0.000025, "0.000025"), (-0.00000012, "-0.00000012"),
                                (0.2, "0.2"), (0, "0"), (True, "1")):
            with self.subTest(value=value):
                self.assertEqual(decimal_text(value, float32=isinstance(value, float)), expected)

    def test_float32_roundtrip_including_subnormal_and_largest_values(self):
        for bits in (0, 1, 0x007fffff, 0x00800000, 0x3e4ccccd, 0x7f7fffff, 0x80000000, 0xbf800001):
            raw = struct.pack("<I", bits)
            value = struct.unpack("<f", raw)[0]
            text = decimal_text(value, float32=True)
            with self.subTest(bits=hex(bits), text=text):
                self.assertNotIn("e", text.lower())
                self.assertEqual(struct.pack("<f", float(text)), raw)

    def test_readout_precision_and_explicit_sign_are_preserved(self):
        self.assertEqual(decimal_text(0.0000123456789, 5), "0.000012346")
        self.assertEqual(decimal_text(80.0, 3, signed=True), "+80")
        self.assertEqual(decimal_text(-0.001234, 3, signed=True), "-0.00123")
