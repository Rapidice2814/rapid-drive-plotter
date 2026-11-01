"""Portable optional presets, readback snapshots and fail-stop application."""
import json
from pathlib import Path
import tempfile
import unittest
from test_protocol import fixture, definitions_from
from test_dynamic_ui import HAS_GUI
from motor_presets import PresetStore
from configuration_protocol import encode_configuration
from protocol_definitions import FOCState, MsgType
from protocol_codec import PIDPayload, RawPacket


class PresetTests(unittest.TestCase):
    def test_update_existing_keeps_identity_and_rejects_paths_outside_preset_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(Path(directory) / "presets", bundled_directory=Path(directory) / "bundled")
            path = store.save("Original", {"motor.pole_pairs": 11})
            self.assertEqual(store.save("Updated", {"motor.pole_pairs": 7}, path=path), path)
            self.assertEqual(len(store.list()[0]), 1)
            self.assertEqual(store.list()[0][0][1]["parameters"]["motor.pole_pairs"], 7)
            outside = Path(directory) / "outside.json"
            outside.write_text("leave alone")
            with self.assertRaises(ValueError): store.save("Bad", path=outside)
            self.assertEqual(outside.read_text(), "leave alone")

    def test_failed_update_preserves_existing_file(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(directory, bundled_directory=Path(directory) / "bundled")
            path = store.save("Original", {"motor.pole_pairs": 11})
            previous = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("test write failure")):
                with self.assertRaises(OSError): store.save("Updated", {"motor.pole_pairs": 7}, path=path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_persistence_optional_fields_safe_names_and_corrupt_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(directory, bundled_directory=Path(directory) / "bundled")
            values = {"motor.pole_pairs": 7, "limits.max_dq_current": 3, "motor.direction": 0}
            path = store.save("../Model / test", values)
            self.assertEqual(path.parent, Path(directory))
            self.assertEqual(PresetStore(directory, bundled_directory=Path(directory) / "bundled").list()[0][0][1]["parameters"], values)
            store.save("Empty", {})
            (Path(directory) / "broken.json").write_text("{broken")
            presets, errors = store.list()
            self.assertEqual(len(presets), 2)
            self.assertEqual(len(errors), 1)
            for value in (float("nan"), float("inf"), "3", None):
                with self.assertRaises(ValueError):
                    store.save("Bad", {"motor.pole_pairs": value})
            self.assertEqual(len(list(Path(directory).glob("*.tmp"))), 0)

    def test_pid_sections_roundtrip_omit_gains_and_reject_invalid_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PresetStore(directory, bundled_directory=Path(directory) / "bundled")
            gains = {"pid_speed": {"kp": 0.1, "kd": 0}}
            store.save("Partial", pids=gains)
            data = PresetStore(directory, bundled_directory=Path(directory) / "bundled").list()[0][0][1]
            self.assertEqual(data["parameters"], {})
            self.assertEqual(data["pids"], gains)
            for invalid in ({"bad": 1}, {"kp": float("nan")}, {"ki": True}, {"kd": None}):
                with self.assertRaises(ValueError): store.save("Bad", pids={"pid_speed": invalid})


class BuiltinPresetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store = PresetStore(root / "user", bundled_directory=root / "built-in")

    def builtin(self, name="Reference motor", parameters=None):
        path = self.store.bundled_directory / "model.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 2, "name": name, "parameters": parameters or {}, "pids": {}}), encoding="utf-8")
        return path

    def test_both_sources_load_even_when_names_and_filenames_match(self):
        builtin = self.builtin("Model", {"motor.pole_pairs": 11})
        user = self.store.save("Model", {"motor.pole_pairs": 7})
        user = user.rename(user.parent / builtin.name)
        presets, errors = self.store.list()
        self.assertFalse(errors)
        self.assertEqual([path for path, _data in presets], [builtin, user])
        self.assertTrue(self.store.is_builtin(builtin))
        self.assertFalse(self.store.can_update(builtin))
        self.assertTrue(self.store.can_update(user))

    def test_builtin_cannot_be_overwritten_and_custom_copy_is_writable(self):
        builtin = self.builtin(parameters={"motor.pole_pairs": 11})
        original = builtin.read_bytes()
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.store.save("Changed", {"motor.pole_pairs": 7}, path=builtin)
        self.assertEqual(builtin.read_bytes(), original)
        user = self.store.save("Custom", {"motor.pole_pairs": 7})
        self.store.save("Custom updated", {"motor.pole_pairs": 9}, path=user)
        self.assertEqual(json.loads(user.read_text())["parameters"]["motor.pole_pairs"], 9)
        self.assertEqual(builtin.read_bytes(), original)

    def test_user_folder_cannot_be_redirected_into_builtins(self):
        builtin = self.builtin()
        original = builtin.read_bytes()
        for user_folder in (self.store.bundled_directory, self.store.bundled_directory / "nested"):
            store = PresetStore(user_folder, bundled_directory=self.store.bundled_directory)
            with self.assertRaisesRegex(ValueError, "read-only"):
                store.save("Custom")
        self.assertEqual(builtin.read_bytes(), original)
        self.assertFalse((self.store.bundled_directory / "nested").exists())

    def test_same_folder_is_listed_once_and_stays_read_only(self):
        builtin = self.builtin()
        store = PresetStore(builtin.parent, bundled_directory=builtin.parent)
        self.assertEqual([path for path, _data in store.list()[0]], [builtin])
        self.assertFalse(store.can_update(builtin))

    def test_restart_reads_changes_to_builtin_file_and_corrupt_files_do_not_hide_valid_presets(self):
        builtin = self.builtin("Original")
        self.store.save("User")
        data = json.loads(builtin.read_text())
        data["name"] = "Renamed"
        builtin.write_text(json.dumps(data), encoding="utf-8")
        (builtin.parent / "bad.json").write_text("{broken")
        (self.store.directory / "bad.json").write_text("{broken")
        again = PresetStore(self.store.directory, bundled_directory=builtin.parent)
        presets, errors = again.list()
        self.assertEqual({data["name"] for _path, data in presets}, {"Renamed", "User"})
        self.assertEqual(len(errors), 2)

    def test_missing_builtin_folder_still_allows_user_presets(self):
        user = self.store.save("User")
        self.assertEqual([path for path, _data in self.store.list()[0]], [user])


@unittest.skipUnless(HAS_GUI, "Install GUI dependencies")
class PresetUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from test_protocol import Harness
        from ui.configuration_panel import ConfigurationDock
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PresetStore(Path(self.directory.name) / "presets", bundled_directory=Path(self.directory.name) / "bundled")
        self.h = Harness(self.directory.name); self.h.start(["tick"])
        self.sent = []
        def send(packet, **callbacks):
            self.sent.append(packet)
            self.h.session.send(packet, **callbacks)
        self.dock = ConfigurationDock(send, preset_store=self.store)
        self.addCleanup(self.dock.deleteLater)
        self.entries, self.pids = self.h.session.definitions.configuration, self.h.session.definitions.pids
        self.dock.set_definitions(self.entries, self.pids)
        self.dock.set_connected(True)
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.pump()
        self.sent.clear()

    def pump(self):
        for _ in range(12):
            self.h.drain()
            self.app.processEvents()

    def edit(self, identifier, value):
        self.dock.widgets[identifier].setText(str(value))
        self.dock._edited(identifier)

    def select(self, path):
        from ui.configuration_panel import PRESET_PATH_ROLE
        self.dock.refresh_presets()
        self.dock.preset_combo.setCurrentIndex(self.dock.preset_combo.findData(str(path.resolve()), PRESET_PATH_ROLE))

    def save_dialog(self, name=None, create_new=False, pid_names=None):
        from unittest.mock import patch
        from ui.motor_preset_dialog import MotorPresetDialog
        def dialog(*args, **kwargs):
            result = MotorPresetDialog(*args, **kwargs)
            self.last_dialog = result
            self.addCleanup(result.deleteLater)
            if create_new: result.target.setCurrentIndex(0)
            if name is not None: result.name.setText(name)
            if pid_names is not None:
                for entry, group, _edits in result.pid_groups:
                    group.setChecked(entry["name"] in pid_names)
            result.exec = lambda: (result._accept() or result.result())
            return result
        with patch("ui.configuration_panel.MotorPresetDialog", side_effect=dialog):
            self.dock.save_preset()

    def test_only_global_actions_and_read_covers_all_configuration_and_pids(self):
        from PySide6.QtWidgets import QPushButton
        texts = [b.text() for b in self.dock.findChildren(QPushButton) if not b.isHidden()]
        self.assertIn("Read from driver", texts)
        self.assertIn("Apply values to RAM", texts)
        self.assertIn("Save preset…", texts)
        self.assertNotIn("Read group", texts)
        self.assertNotIn("Apply edited settings to RAM", texts)
        self.assertTrue(self.dock.pid_controls.pid_refresh_btn.isHidden())
        self.assertTrue(self.dock.pid_controls.pid_send_btn.isHidden())
        self.dock.read_from_driver()
        self.pump()
        self.assertEqual({p.data[0] for p in self.sent if p.msg_type == MsgType.MSG_GET_CONFIGURATION}, set(self.dock.groups))
        self.assertEqual({p.data.controller_id for p in self.sent if p.msg_type == MsgType.MSG_GET_PID}, {e["id"] for e in self.pids})
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertEqual(len(self.dock.editor_parameters()), len(self.entries))
        self.assertEqual(len(self.dock.pid_controls.editor_gains()), len(self.pids))
        self.assertFalse(list(self.store.directory.glob("*.json")))

    def test_edit_marks_dirty_after_read_and_revert_clears_marker(self):
        self.assertEqual(self.dock.widgets[9].text(), "0.2")
        self.assertEqual(self.dock.widgets[6].text(), "80")
        self.assertEqual(self.dock.widgets[10].text(), "0.000025")
        original = self.dock.values[0]
        self.edit(0, 3)
        self.assertEqual(self.dock.dirty, {0})
        self.assertTrue(self.dock.widgets[0].property("dirty"))
        self.assertNotIn("Changed", self.dock.labels[0].text())
        self.assertIn("color:", self.dock.labels[0].styleSheet())
        self.assertNotIn("border", self.dock.widgets[0].styleSheet())
        self.assertFalse(self.dock.reset_buttons[0].isHidden())
        self.assertTrue(self.dock.apply_driver_button.isEnabled())
        self.edit(0, original)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.widgets[0].property("dirty"))
        self.assertTrue(self.dock.reset_buttons[0].isHidden())
        self.assertFalse(self.dock.apply_driver_button.isEnabled())
        field = self.dock.pid_controls.pid_widgets[2]["kp"]
        baseline = field.value()
        field.setValue(0.2)
        self.assertEqual(self.dock.pid_controls.modified, {(2, "kp")})
        self.assertTrue(field.property("dirty"))
        field.setValue(baseline)
        self.assertFalse(self.dock.pid_controls.modified)

    def test_editing_is_allowed_in_every_motor_state_but_mixed_apply_requires_idle(self):
        self.edit(0, 3)
        self.dock.pid_controls.pid_widgets[2]["kp"].setValue(0.2)
        for state in FOCState:
            if state == FOCState.FOC_STATE_COUNT:
                continue
            with self.subTest(state=state):
                self.dock.set_state(state)
                self.assertTrue(all(widget.isEnabled() for widget in self.dock.widgets.values()))
                self.assertTrue(all(field.isEnabled() for fields in self.dock.pid_controls.pid_widgets.values() for field in fields.values()))
                self.assertTrue(self.dock.save_preset_button.isEnabled())
                self.assertEqual(self.dock.apply_driver_button.isEnabled(), state == FOCState.FOC_STATE_IDLE)
                if state != FOCState.FOC_STATE_IDLE:
                    self.dock.apply_to_driver()
                    self.dock.pid_controls.apply()
                    self.assertFalse(self.sent)
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.dock.apply_to_driver(); self.pump()
        self.assertEqual(self.dock.values[0], 3)
        self.assertAlmostEqual(self.h.simulator._pids[2][0], 0.2)
        self.assertFalse(self.h.rejections)

    def test_running_allows_local_reset_and_saving_presets_without_driver_writes(self):
        self.dock.set_state(FOCState.FOC_STATE_RUN)
        self.edit(0, 3)
        self.dock.pid_controls.pid_widgets[2]["kp"].setValue(0.2)
        self.dock.reset_buttons[0].click()
        self.dock.pid_controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(self.dock.widgets[0].text(), "5")
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.edit(0, 3)
        self.save_dialog("Prepared while running", create_new=True)
        self.assertEqual(self.store.list()[0][0][1]["parameters"]["limits.max_dq_current"], 3)
        self.assertTrue(self.dock.has_unapplied_changes())
        self.assertFalse(self.dock.apply_driver_button.isEnabled())
        self.assertFalse(self.sent)
        self.dock.set_state(FOCState.FOC_STATE_IDLE)
        self.assertTrue(self.dock.apply_driver_button.isEnabled())

    def test_invalid_values_are_highlighted_and_cannot_be_applied_or_saved(self):
        entry = next(e for e in self.entries if e["id"] == 0)
        for invalid in ("15a", "", "nan", "inf", "-1", "1e99", str(float(entry["maximum"]) + 1)):
            with self.subTest(value=invalid):
                self.edit(0, invalid)
                self.assertTrue(self.dock.widgets[0].property("invalid"))
                self.assertIn("Invalid value", self.dock.widgets[0].toolTip())
                self.assertFalse(self.dock.apply_driver_button.isEnabled())
                self.assertFalse(self.dock.save_preset_button.isEnabled())
                self.dock.apply_to_driver()
                self.assertFalse(self.sent)
        self.dock.reset_buttons[0].click()
        self.assertEqual(self.dock.widgets[0].text(), "5")
        self.assertFalse(self.dock.widgets[0].property("invalid"))
        self.assertFalse(self.dock.widgets[0].styleSheet())
        self.edit(0, "0")  # Zero remains the valid unset value.
        self.assertFalse(self.dock.widgets[0].property("invalid"))
        self.edit(0, "3")
        self.assertFalse(self.dock.widgets[0].property("invalid"))
        self.assertTrue(self.dock.apply_driver_button.isEnabled())
        self.assertTrue(self.dock.save_preset_button.isEnabled())
        self.dock.apply_to_driver(); self.pump()
        self.assertEqual(self.dock.values[0], 3)

    def test_reset_buttons_restore_read_values_locally_including_checkbox_and_pid(self):
        self.edit(0, 3)
        self.dock.widgets[8].click()
        field = self.dock.pid_controls.pid_widgets[2]["kp"]
        original_gain = field.value()
        field.setValue(0.2)
        self.dock.reset_buttons[0].click()
        self.dock.reset_buttons[8].click()
        self.dock.pid_controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(float(self.dock.widgets[0].text()), 5)
        self.assertFalse(self.dock.widgets[8].isChecked())
        self.assertEqual(field.value(), original_gain)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertFalse(self.dock.has_unapplied_changes())
        self.assertFalse(self.sent)

    def test_reset_restores_selected_preset_and_keeps_its_pending_driver_values(self):
        path = self.store.save("Model", {"motor.pole_pairs": 7, "motor.direction": 1}, {"pid_speed": {"kp": 0.2}})
        self.select(path)
        self.edit(7, 9)
        self.dock.widgets[8].click()
        field = self.dock.pid_controls.pid_widgets[2]["kp"]
        field.setValue(0.3)
        self.dock.reset_buttons[7].click()
        self.dock.reset_buttons[8].click()
        self.dock.pid_controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(float(self.dock.widgets[7].text()), 7)
        self.assertTrue(self.dock.widgets[8].isChecked())
        self.assertEqual(field.value(), 0.2)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(self.dock.has_unapplied_changes())
        self.assertFalse(self.sent)

    def test_reset_after_apply_stages_local_revert_and_is_disabled_during_write(self):
        self.edit(0, 3)
        self.dock.apply_to_driver()
        self.assertFalse(self.dock.reset_buttons[0].isEnabled())
        self.dock.reset_setting(0)
        self.assertEqual(float(self.dock.widgets[0].text()), 3)
        self.pump()
        self.assertTrue(self.dock.reset_buttons[0].isEnabled())
        self.sent.clear()
        self.dock.reset_buttons[0].click()
        self.assertEqual(float(self.dock.widgets[0].text()), 5)
        self.assertEqual(self.dock.values[0], 3)
        self.assertTrue(self.dock.has_unapplied_changes())
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.sent)

    def test_selection_loads_clean_baseline_but_global_apply_writes_preset_and_pid(self):
        self.h.simulator._pids[2] = (0.8, 7, 0.6)
        path = self.store.save("Model", {"motor.pole_pairs": 7, "limits.max_dq_current": 3},
                               {"pid_speed": {"kp": 0.2, "kd": 0}})
        self.select(path)
        self.assertFalse(self.sent)
        self.assertEqual(float(self.dock.widgets[7].text()), 7)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(self.dock.apply_driver_button.isEnabled())
        self.dock.apply_to_driver(); self.pump()
        self.assertFalse(self.dock.applying)
        self.assertFalse(self.dock.has_unapplied_changes())
        self.assertEqual(self.dock.values[0], 3)
        self.assertEqual(self.dock.values[7], 7)
        self.assertAlmostEqual(self.h.simulator._pids[2][0], 0.2)
        self.assertEqual(self.h.simulator._pids[2][1:], (7, 0))
        self.assertFalse(self.h.errors)
        self.assertFalse(self.h.rejections)

    def test_edits_after_loading_compare_to_preset_and_revert_to_it(self):
        path = self.store.save("Model", {"motor.pole_pairs": 7}, {"pid_speed": {"kp": 0.2}})
        self.select(path)
        self.edit(7, 9)
        self.assertEqual(self.dock.dirty, {7})
        self.edit(7, 7)
        self.assertFalse(self.dock.dirty)
        field = self.dock.pid_controls.pid_widgets[2]["kp"]
        field.setValue(0.3)
        self.assertEqual(self.dock.pid_controls.modified, {(2, "kp")})
        field.setValue(0.2)
        self.assertFalse(self.dock.pid_controls.modified)

    def test_driver_read_keeps_selected_preset_as_reference_and_reset_uses_preset(self):
        from unittest.mock import patch
        path = self.store.save("Reference", {"limits.max_dq_current": 3}, {"pid_speed": {"kp": 0.2}})
        self.select(path)
        self.dock.read_from_driver(); self.pump()
        self.assertEqual(self.dock.selected_path, path)
        self.assertEqual(self.dock.widgets[0].text(), "5")
        self.assertEqual(self.dock.dirty, {0})
        self.assertEqual(self.dock.pid_controls.modified, {(2, "kp")})
        self.assertFalse(self.dock.has_unapplied_changes())
        self.assertFalse(self.dock.apply_driver_button.isEnabled())
        # Differences from the preset are not unsent local edits. Reading again
        # must not ask to discard edits or erase the comparison.
        with patch("ui.configuration_panel.QMessageBox.question", side_effect=AssertionError("Unexpected prompt")):
            self.dock.read_from_driver(); self.pump()
        self.assertEqual(self.dock.dirty, {0})
        self.sent.clear()
        self.dock.reset_buttons[0].click()
        self.dock.pid_controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(self.dock.widgets[0].text(), "3")
        self.assertEqual(self.dock.pid_controls.pid_widgets[2]["kp"].value(), 0.2)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(self.dock.apply_driver_button.isEnabled())
        self.assertFalse(self.sent)

    def test_missing_scalar_and_individual_gains_are_grey_while_zero_is_included(self):
        from ui.setting_label import OMITTED_COLOR
        path = self.store.save("Partial", {"motor.pole_pairs": 7}, {"pid_speed": {"kp": 0}})
        self.select(path)
        self.dock.read_from_driver(); self.pump()
        self.assertTrue(self.dock.widgets[0].property("omitted"))
        self.assertIn(OMITTED_COLOR, self.dock.labels[0].styleSheet())
        self.assertFalse(self.dock.widgets[7].property("omitted"))
        self.assertEqual(self.dock.dirty, {7})
        for gain in ("ki", "kd"):
            self.assertTrue(self.dock.pid_controls.pid_widgets[2][gain].property("omitted"))
            self.assertIn(OMITTED_COLOR, self.dock.pid_controls.labels[(2, gain)].styleSheet())
        self.assertFalse(self.dock.pid_controls.pid_widgets[2]["kp"].property("omitted"))
        self.assertEqual(self.dock.pid_controls.modified, {(2, "kp")})
        self.assertTrue(self.dock.pid_controls.controller_labels[0].property("omitted"))
        self.assertIn(OMITTED_COLOR, self.dock.pid_controls.controller_labels[0].styleSheet())
        self.assertFalse(self.dock.pid_controls.controller_labels[2].property("omitted"))
        self.dock.pid_controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(self.dock.pid_controls.pid_widgets[2]["kp"].value(), 0)
        self.assertFalse(self.dock.pid_controls.modified)

    def test_omitted_fields_can_be_edited_and_reset_without_becoming_preset_differences(self):
        path = self.store.save("Partial", {"motor.pole_pairs": 7})
        self.select(path)
        self.sent.clear()
        original_gain = self.dock.pid_controls.pid_widgets[2]["ki"].value()
        self.edit(0, 3)
        self.dock.pid_controls.pid_widgets[2]["ki"].setValue(8)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(self.dock.widgets[0].property("omitted"))
        self.assertFalse(self.dock.reset_buttons[0].isHidden())
        self.assertFalse(self.dock.pid_controls.reset_buttons[(2, "ki")].isHidden())
        self.dock.reset_buttons[0].click()
        self.dock.pid_controls.reset_buttons[(2, "ki")].click()
        self.assertEqual(self.dock.widgets[0].text(), "5")
        self.assertEqual(self.dock.pid_controls.pid_widgets[2]["ki"].value(), original_gain)
        self.assertFalse(self.sent)

    def test_clearing_selection_restores_driver_baseline_and_removes_grey_markers(self):
        path = self.store.save("Partial", {"motor.pole_pairs": 7}, {"pid_speed": {"kp": 0.2}})
        self.select(path)
        self.dock.read_from_driver(); self.pump()
        self.assertTrue(self.dock.dirty)
        self.dock.preset_combo.setCurrentIndex(0)
        self.assertIsNone(self.dock.reference)
        self.assertIsNone(self.dock.pid_controls.reference)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(all(not widget.property("omitted") for widget in self.dock.widgets.values()))
        self.assertTrue(all(not field.property("omitted") for fields in self.dock.pid_controls.pid_widgets.values() for field in fields.values()))

    def test_saving_selects_whole_pid_without_adding_other_controllers_or_scalar_fields(self):
        path = self.store.save("Partial", {"motor.pole_pairs": 7}, {"pid_speed": {"kp": 0.2}})
        self.select(path)
        self.dock.read_from_driver(); self.pump()
        self.save_dialog()
        saved = json.loads(path.read_text())
        self.assertEqual(saved["parameters"], {"motor.pole_pairs": 11})
        self.assertEqual(set(saved["pids"]), {"pid_speed"})
        self.assertEqual(set(saved["pids"]["pid_speed"]), {"kp", "ki", "kd"})
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(self.dock.widgets[0].property("omitted"))
        self.assertFalse(self.dock.pid_controls.pid_widgets[2]["ki"].property("omitted"))
        self.assertTrue(self.dock.pid_controls.controller_labels[0].property("omitted"))

    def test_controller_heading_highlights_differences_and_resets_all_gains_locally(self):
        path = self.store.save("Speed PID", {}, {"pid_speed": {"kp": 0.2, "ki": 8, "kd": 0.3}})
        self.select(path)
        self.dock.read_from_driver(); self.pump()
        controls = self.dock.pid_controls
        self.assertTrue(controls.controller_labels[2].property("dirty"))
        self.assertFalse(controls.controller_reset_buttons[2].isHidden())
        self.assertTrue(all(not controls.reset_buttons[(2, g)].isHidden() for g in ("kp", "ki", "kd")))
        self.sent.clear()
        controls.controller_reset_buttons[2].click()
        self.assertEqual({g: w.value() for g, w in controls.pid_widgets[2].items()}, {"kp": 0.2, "ki": 8, "kd": 0.3})
        self.assertFalse(controls.modified)
        self.assertFalse(controls.controller_labels[2].property("dirty"))
        self.assertTrue(controls.controller_reset_buttons[2].isHidden())
        self.assertTrue(self.dock.has_unapplied_changes())
        self.assertFalse(self.sent)
        controls.pid_widgets[2]["kp"].setValue(0.4)
        self.assertTrue(controls.controller_labels[2].property("dirty"))
        controls.reset_buttons[(2, "kp")].click()
        self.assertEqual(controls.pid_widgets[2]["kp"].value(), 0.2)
        self.assertFalse(controls.controller_labels[2].property("dirty"))

    def test_unchecking_a_pid_excludes_its_entire_gain_set(self):
        path = self.store.save("Speed PID", {}, {"pid_speed": {"kp": 0.2, "ki": 8, "kd": 0}})
        self.select(path)
        self.save_dialog(pid_names=[])
        self.assertEqual(json.loads(path.read_text())["pids"], {})
        self.assertTrue(self.dock.pid_controls.controller_labels[2].property("omitted"))
        self.assertFalse(self.sent)

    def test_applying_only_a_reset_gain_preserves_other_driver_gains_and_reference_differences(self):
        self.h.simulator._pids[2] = (0.8, 7, 0.6)
        path = self.store.save("Partial", {}, {"pid_speed": {"kp": 0.2, "kd": 0}})
        self.select(path)
        self.dock.read_from_driver(); self.pump()
        self.assertEqual(self.dock.pid_controls.modified, {(2, "kp"), (2, "kd")})
        self.dock.pid_controls.reset_buttons[(2, "kd")].click()
        self.dock.apply_to_driver(); self.pump()
        self.assertAlmostEqual(self.h.simulator._pids[2][0], 0.8)
        self.assertEqual(self.h.simulator._pids[2][1:], (7, 0))
        self.assertEqual(self.dock.pid_controls.modified, {(2, "kp")})
        self.assertFalse(self.dock.has_unapplied_changes())

    def test_read_replaces_editor_after_confirmation_without_saving_a_file(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.edit(0, 3)
        self.dock.pid_controls.pid_widgets[2]["kp"].setValue(0.2)
        with patch("ui.configuration_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.dock.read_from_driver()
        self.assertFalse(self.sent)
        with patch("ui.configuration_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.dock.read_from_driver()
        self.pump()
        self.assertEqual(float(self.dock.widgets[0].text()), 5)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertFalse(self.dock.has_unapplied_changes())
        self.assertFalse(list(self.store.directory.glob("*.json")))

    def test_save_uses_editor_values_updates_existing_file_and_can_create_new(self):
        path = self.store.save("Existing", {"limits.max_dq_current": 5})
        self.select(path)
        self.edit(0, 3)
        self.dock.pid_controls.pid_widgets[2]["kp"].setValue(0.2)
        self.save_dialog()
        saved = json.loads(path.read_text())
        self.assertEqual(saved["parameters"]["limits.max_dq_current"], 3)
        self.assertEqual(saved["pids"]["pid_speed"]["kp"], 0.2)
        self.assertEqual(len(list(self.store.directory.glob("*.json"))), 1)
        self.assertFalse(self.sent)  # Saving never reads or applies driver RAM.
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.pid_controls.modified)
        self.assertTrue(self.dock.has_unapplied_changes())
        self.save_dialog("Another model", create_new=True)
        self.assertEqual(len(list(self.store.directory.glob("*.json"))), 2)
        self.assertEqual({data["name"] for _path, data in self.store.list()[0]}, {"Existing", "Another model"})

    def test_builtin_selection_loads_readonly_template_and_save_creates_user_copy(self):
        builtin = self.store.bundled_directory / "reference.json"
        builtin.parent.mkdir(parents=True)
        builtin.write_text(json.dumps({"version": 2, "name": "Reference", "parameters": {"motor.pole_pairs": 7},
                                       "pids": {"pid_speed": {"kp": 0.2}}}), encoding="utf-8")
        previous = builtin.read_bytes()
        existing_user = self.store.save("Existing user", {"motor.pole_pairs": 11})
        self.select(builtin)
        self.assertEqual(self.dock.preset_combo.currentText(), "Reference (built-in)")
        self.assertEqual(float(self.dock.widgets[7].text()), 7)
        self.edit(7, 9)
        self.save_dialog()
        self.assertIsNone(self.last_dialog.path)
        self.assertEqual(self.last_dialog.target.itemData(1), existing_user)
        self.assertEqual(self.last_dialog.target.count(), 2)  # New or existing user; no built-in.
        self.assertEqual(builtin.read_bytes(), previous)
        user = self.dock.selected_path
        self.assertEqual(user.parent, self.store.directory)
        saved = json.loads(user.read_text())
        self.assertEqual(saved["name"], "Reference (custom)")
        self.assertEqual(saved["parameters"]["motor.pole_pairs"], 9)
        self.assertEqual(saved["pids"]["pid_speed"]["kp"], 0.2)
        self.assertFalse(self.store.is_builtin(user))
        self.assertFalse(self.sent)

    def test_builtin_and_user_names_are_distinguishable_and_reload_reads_builtin_rename(self):
        builtin = self.store.bundled_directory / "model.json"
        builtin.parent.mkdir(parents=True)
        data = {"version": 2, "name": "Model", "parameters": {}, "pids": {}}
        builtin.write_text(json.dumps(data), encoding="utf-8")
        self.store.save("Model")
        self.dock.refresh_presets()
        self.assertEqual([self.dock.preset_combo.itemText(i) for i in range(1, 3)], ["Model (built-in)", "Model"])
        data["name"] = "Renamed"
        builtin.write_text(json.dumps(data), encoding="utf-8")
        self.dock.refresh_presets()
        self.assertEqual(self.dock.preset_combo.itemText(1), "Renamed (built-in)")

    def test_read_failure_retains_editor_and_disconnect_ignores_late_callbacks(self):
        self.edit(0, 3)
        self.dock.send = lambda packet, *, completed, received=None: completed(False)
        self.dock.read_from_driver(confirm=False); self.pump()
        self.assertIn("Read failed", self.dock.status.text())
        self.assertEqual(float(self.dock.widgets[0].text()), 3)
        self.assertEqual(self.dock.dirty, {0})
        self.dock.send = self.h.session.send
        self.dock.read_from_driver(confirm=False)
        self.dock.set_connected(False); self.pump()
        self.assertFalse(self.dock.values)
        self.assertFalse(self.dock.dirty)
        self.assertFalse(self.dock.busy)

    def test_rejected_apply_stops_before_later_groups_and_retains_values(self):
        path = self.store.save("Model", {"motor.pole_pairs": 7, "limits.max_dq_current": 3})
        self.select(path)
        self.dock.send = lambda packet, *, completed, received=None: completed(False)
        self.dock.apply_to_driver()
        self.assertFalse(self.dock.applying)
        self.assertFalse(self.dock.apply_queue)
        self.assertTrue(self.dock.has_unapplied_changes())
        self.assertEqual(float(self.dock.widgets[7].text()), 7)
        self.assertIn("Apply stopped", self.dock.status.text())

    def test_later_group_failure_preserves_earlier_success_and_remaining_editor_values(self):
        path = self.store.save("Model", {"motor.pole_pairs": 7, "limits.max_dq_current": 3})
        self.select(path)
        def send(packet, *, completed, received=None):
            if packet.msg_type == MsgType.MSG_SET_CONFIGURATION and packet.data[0] == 1:
                completed(False)
            else:
                self.h.session.send(packet, completed=completed, received=received)
        self.dock.send = send
        self.dock.apply_to_driver(); self.pump()
        self.assertEqual(self.dock.values[0], 3)
        self.assertEqual(self.dock.values[7], 11)
        self.assertEqual(float(self.dock.widgets[7].text()), 7)
        self.assertTrue(self.dock.has_unapplied_changes())
        self.assertFalse(self.dock.applying)

    def test_global_apply_does_not_stage_untouched_fields_changed_elsewhere(self):
        self.edit(0, 3)
        self.h.simulator._configuration[6] = 70
        self.dock.apply_to_driver(); self.pump()
        self.assertEqual(self.dock.values[6], 70)
        self.assertEqual(float(self.dock.widgets[6].text()), 80)
        self.assertFalse(self.dock.has_unapplied_changes())
        self.assertFalse(self.dock.apply_driver_button.isEnabled())

    def test_cancel_preset_selection_keeps_previous_selection_and_edits(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        first = self.store.save("First", {"motor.pole_pairs": 7})
        second = self.store.save("Second", {"motor.pole_pairs": 9})
        self.select(first); self.edit(7, 8)
        with patch("ui.configuration_panel.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.select(second)
        self.assertEqual(self.dock.selected_path, first)
        self.assertEqual(self.dock.preset_combo.currentText(), "First")
        self.assertEqual(float(self.dock.widgets[7].text()), 8)
        self.assertEqual(self.dock.dirty, {7})

    def test_invalid_preset_retains_previous_editor_and_selection(self):
        good = self.store.save("Good", {"motor.pole_pairs": 7})
        self.select(good)
        bad = self.store.save("Bad", {"motor.pole_pairs": 9999})
        self.select(bad)
        self.assertEqual(float(self.dock.widgets[7].text()), 7)
        self.assertEqual(self.dock.selected_path, good)
        self.assertIn("not loaded", self.dock.status.text())

    def test_optional_dialog_fields_and_existing_target(self):
        from ui.motor_preset_dialog import MotorPresetDialog
        path = self.store.save("Existing", {"motor.pole_pairs": 7})
        dialog = MotorPresetDialog(self.entries, pid_entries=self.pids,
                                   presets=self.store.list()[0], selected_path=path)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.target.currentData(), path)
        self.assertEqual(dialog.name.text(), "Existing")
        dialog._accept()
        self.assertEqual(dialog.parameters, {})
        self.assertEqual(dialog.pids, {})
        entry, group, edits = dialog.pid_groups[0]
        group.setChecked(True)
        for gain, value in (("kp", 0), ("ki", 1), ("kd", 2)):
            edits[gain].setText(str(value))
        dialog._accept()
        self.assertEqual(dialog.pids, {entry["name"]: {"kp": 0, "ki": 1, "kd": 2}})

    def test_selected_pid_requires_all_gains_and_unread_pid_cannot_be_saved(self):
        from ui.motor_preset_dialog import MotorPresetDialog
        entry = self.pids[0]
        dialog = MotorPresetDialog([], pid_entries=[entry])
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText("Complete PID")
        _entry, group, edits = dialog.pid_groups[0]
        group.setChecked(True)
        edits["kp"].setText("0.2")
        dialog._accept()
        self.assertTrue(dialog.error.text())
        self.assertFalse(hasattr(dialog, "pids"))
        edits["ki"].setText("0"); edits["kd"].setText("0")
        dialog._accept()
        self.assertEqual(dialog.pids[entry["name"]], {"kp": 0.2, "ki": 0, "kd": 0})
        unread = MotorPresetDialog([], pid_entries=[entry], pid_values={entry["name"]: {"kp": 0.2}}, editable=False)
        self.addCleanup(unread.deleteLater)
        self.assertFalse(unread.pid_groups[0][1].isEnabled())
        self.assertFalse(unread.pid_groups[0][1].isChecked())

    def test_offline_json_save_requires_complete_pid_objects(self):
        from ui.motor_preset_dialog import MotorPresetDialog
        dialog = MotorPresetDialog([])
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText("Offline")
        dialog.json_edit.setPlainText(json.dumps({"pids": {"pid_speed": {"kp": 0.2}}}))
        dialog._accept()
        self.assertIn("Kp, Ki and Kd together", dialog.error.text())
        dialog.json_edit.setPlainText(json.dumps({"pids": {"pid_speed": {"kp": 0.2, "ki": 0, "kd": 0}}}))
        dialog._accept()
        self.assertEqual(dialog.pids, {"pid_speed": {"kp": 0.2, "ki": 0, "kd": 0}})
