"""Fault latching, acknowledgement, recovery, and firmware flag contracts."""
import os
from pathlib import Path
import re
import tempfile
import unittest

from test_protocol import Harness, ROOT
from protocol_codec import Packet, StatePayload
from protocol_definitions import CommandErrorCode, FOCError, FOCState, MsgType


class FaultRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.h = Harness(temporary.name)
        self.h.start(["tick"])
        self.driver = self.h.simulator

    def command(self, kind, data=None):
        self.h.session.send(Packet(kind, data))
        self.h.drain()
        self.assertFalse(self.h.errors)
        self.assertTrue(self.h.session.active)

    def test_operation_failure_sets_shared_latched_bit_and_enters_error(self):
        self.driver._operation_failed()
        self.assertEqual(self.driver._latched_errors, 0x8000)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)
        self.command(MsgType.MSG_GET_STATE)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)
        self.assertEqual(self.driver._latched_errors, FOCError.FOC_ERROR_OPERATION_FAILED)

    def test_clearing_operation_failure_goes_to_stop_not_run(self):
        self.driver._operation_failed()
        self.command(MsgType.MSG_CLEAR_LATCHED_ERRORS)
        self.assertEqual(self.driver._latched_errors, 0)
        self.assertEqual(self.driver._active_errors, 0)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_STOP)
        self.assertIsNone(self.driver._control_mode)

    def test_active_fault_relatches_immediately_when_clear_is_requested(self):
        fault = int(FOCError.FOC_ERROR_DRIVER_FAULT)
        self.driver._active_errors = fault
        self.command(MsgType.MSG_GET_STATE)
        self.command(MsgType.MSG_CLEAR_LATCHED_ERRORS)
        self.assertEqual(self.driver._active_errors, fault)
        self.assertEqual(self.driver._latched_errors, fault)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)

    def test_resolved_fault_requires_acknowledgement_before_recovery(self):
        self.driver._active_errors = int(FOCError.FOC_ERROR_VBUS_UV)
        self.command(MsgType.MSG_GET_STATE)
        self.driver._active_errors = 0
        self.command(MsgType.MSG_GET_STATE)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)
        self.command(MsgType.MSG_CLEAR_LATCHED_ERRORS)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_STOP)

    def test_clear_removes_old_event_but_keeps_present_fault(self):
        self.driver._operation_failed()
        self.driver._active_errors = int(FOCError.FOC_ERROR_MOTOR_OT)
        self.command(MsgType.MSG_CLEAR_LATCHED_ERRORS)
        self.assertEqual(self.driver._latched_errors, FOCError.FOC_ERROR_MOTOR_OT)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)

    def test_error_cannot_be_requested_without_a_latched_fault(self):
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_ERROR))
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_IDLE)
        self.assertEqual(self.driver._latched_errors, 0)
        self.assertIn(CommandErrorCode.INVALID_STATE.name, self.h.rejections[-1])
        self.assertIn("latched fault", self.h.rejections[-1])

    def test_state_commands_cannot_bypass_latched_fault(self):
        self.driver._operation_failed()
        for state in (FOCState.FOC_STATE_RUN, FOCState.FOC_STATE_STOP, FOCState.FOC_STATE_IDLE,
                      FOCState.FOC_STATE_PID_AUTOTUNE, FOCState.FOC_STATE_ALIGNMENT):
            with self.subTest(state=state):
                self.command(MsgType.MSG_SET_STATE, StatePayload(state))
                self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)
                self.assertIn(CommandErrorCode.LATCHED_FAULTS.name, self.h.rejections[-1])

    def test_flash_commands_are_available_with_active_or_latched_faults(self):
        for active in (0, int(FOCError.FOC_ERROR_DRIVER_FAULT)):
            for command in (MsgType.MSG_FLASH_SAVE, MsgType.MSG_FLASH_LOAD, MsgType.MSG_FLASH_CLEAR):
                with self.subTest(active=active, command=command):
                    self.driver._operation_failed()
                    self.driver._active_errors = active
                    before = len(self.h.rejections)
                    self.command(command)
                    self.assertEqual(len(self.h.rejections), before)
                    self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)
                    self.assertTrue(self.driver._latched_errors & FOCError.FOC_ERROR_OPERATION_FAILED)
                    self.assertEqual(self.driver._active_errors, active)

    def test_flash_save_load_and_clear_work_from_error(self):
        self.driver._operation_failed()
        identifier = next(iter(self.driver._configuration))
        original = self.driver._configuration[identifier]
        self.driver._configuration[identifier] = original + 1
        self.command(MsgType.MSG_FLASH_SAVE)
        self.driver._configuration[identifier] = original + 2
        self.command(MsgType.MSG_FLASH_LOAD)
        self.assertEqual(self.driver._configuration[identifier], original + 1)
        self.command(MsgType.MSG_FLASH_CLEAR)
        self.assertEqual(self.driver._saved_configuration[identifier], original)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)

    def test_bootloader_available_from_error_and_not_preempted_by_faults(self):
        self.driver._operation_failed()
        self.driver._active_errors = int(FOCError.FOC_ERROR_DRIVER_FAULT)
        self.command(MsgType.MSG_ENTER_BOOTLOADER)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_BOOTLOADER)
        self.assertFalse(self.driver._logging)
        self.command(MsgType.MSG_GET_STATE)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_BOOTLOADER)
        self.assertTrue(self.driver._latched_errors)

    def test_state_selector_maintenance_requests_follow_same_rules(self):
        self.driver._operation_failed()
        for state in (FOCState.FOC_STATE_FLASH_SAVE, FOCState.FOC_STATE_FLASH_LOAD, FOCState.FOC_STATE_FLASH_CLEAR):
            before = len(self.h.rejections)
            self.command(MsgType.MSG_SET_STATE, StatePayload(state))
            self.assertEqual(len(self.h.rejections), before)
            self.assertEqual(self.driver._state, FOCState.FOC_STATE_ERROR)
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_BOOTLOADER))
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_BOOTLOADER)

    def test_maintenance_still_rejected_while_running(self):
        self.command(MsgType.MSG_SET_STATE, StatePayload(FOCState.FOC_STATE_RUN))
        for command in (MsgType.MSG_FLASH_SAVE, MsgType.MSG_FLASH_LOAD, MsgType.MSG_FLASH_CLEAR,
                        MsgType.MSG_ENTER_BOOTLOADER):
            self.command(command)
            self.assertEqual(self.driver._state, FOCState.FOC_STATE_RUN)
            self.assertIn(CommandErrorCode.MUST_BE_IDLE.name, self.h.rejections[-1])
            self.assertIn("IDLE or ERROR", self.h.rejections[-1])

    def test_fault_free_error_state_recovers_to_stop(self):
        self.driver._state = int(FOCState.FOC_STATE_ERROR)
        self.command(MsgType.MSG_GET_STATE)
        self.assertEqual(self.driver._state, FOCState.FOC_STATE_STOP)


class FirmwareFaultContractTests(unittest.TestCase):
    def setUp(self):
        self.firmware = Path(os.environ.get("RAPID_DRIVE_FIRMWARE", ROOT.parent / "rapid-drive-foc"))
        if not (self.firmware / "Foc/FOC_Core/FOC_Diagnostics.h").exists():
            self.skipTest("Firmware checkout not available")

    def test_all_fault_bits_match_the_plotter(self):
        source = (self.firmware / "Foc/FOC_Core/FOC_Diagnostics.h").read_text()
        flags = re.findall(r"#define\s+(FOC_ERROR_\w+)\s+\(1u\s*<<\s*(\d+)\)", source)
        self.assertEqual(len(flags), len(FOCError))
        for name, bit in flags:
            self.assertEqual(int(getattr(FOCError, name)), 1 << int(bit), name)
        self.assertEqual(FOCError.FOC_ERROR_OPERATION_FAILED, 0x8000)

    def test_operation_failures_latch_instead_of_entering_unexplained_error(self):
        for filename, count in (("FOC_States_Calibration.c", 7), ("FOC_States_Storage.c", 3)):
            source = (self.firmware / "Foc/FOC_States" / filename).read_text()
            self.assertEqual(source.count("FOC_SetError(hfoc, FOC_ERROR_OPERATION_FAILED)"), count)
            self.assertNotIn("FOC_SetState(hfoc, FOC_STATE_ERROR", source)

    def test_flash_critical_sections_have_one_cleanup_exit(self):
        source = (self.firmware / "Foc/FOC_Drivers/FOC_Flash.c").read_text()
        for function in ("FOC_FLASH_WriteData", "FOC_FLASH_ClearData"):
            body = re.search(r"FLASH_StatusTypeDef " + function + r"\([^)]*\)\{(.*?)\n\}", source, re.S).group(1)
            critical = body.split("__disable_irq();", 1)[1]
            self.assertEqual(critical.count("return "), 1)
            self.assertIn("goto cleanup;", critical)
            self.assertIn("HAL_FLASH_Lock()", critical)
            self.assertIn("__set_PRIMASK(interrupt_mask);", critical)


if __name__ == "__main__":
    unittest.main()
