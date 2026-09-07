from __future__ import annotations

import unittest

from installer.state_machine import STATES, InstallerStateMachine, StateError


class StateMachineTests(unittest.TestCase):
    def test_exact_happy_path(self) -> None:
        machine = InstallerStateMachine()
        for state in STATES[1:]:
            machine.transition(state)
        self.assertEqual(machine.state, "thingino_booted")

    def test_skipped_state_is_rejected(self) -> None:
        machine = InstallerStateMachine()
        with self.assertRaises(StateError):
            machine.transition("installer_booted")

    def test_fixed_failure_is_terminal(self) -> None:
        machine = InstallerStateMachine()
        machine.fail("host_validation")
        self.assertEqual(machine.state, "failed_host_validation")
        with self.assertRaises(StateError):
            machine.transition("bootstrap_package_ready")


if __name__ == "__main__":
    unittest.main()
