from datetime import datetime, timedelta, timezone

import pytest

from aigenreqsample.controller import ChildLockController
from aigenreqsample.model import (
    AdultOccupantInput,
    BodyFeedback,
    BodyResult,
    ChangeReason,
    Config,
    FaultCode,
    HmiInput,
    PowerRestorePolicy,
    Seat,
    VoiceInput,
)


UTC = timezone.utc


def ts(seconds: int) -> datetime:
    return datetime(2026, 3, 20, tzinfo=UTC) + timedelta(seconds=seconds)


def test_manual_hmi_activation_updates_left_seat_and_body_command() -> None:
    controller = ChildLockController()
    controller.complete_startup_checks()

    command = controller.handle_hmi_input(HmiInput(seat=Seat.LEFT, requested_lock=True, timestamp=ts(0)))

    assert command == BodyResult(
        command=controller.last_body_command,
        status_view=controller.status_view,
        changed_seats={Seat.LEFT},
    )
    assert controller.status_by_seat[Seat.LEFT].locked is True
    assert controller.last_body_command.door_locked[Seat.LEFT] is True
    assert controller.last_body_command.window_locked[Seat.LEFT] is True
    assert controller.status_view.reason_by_seat[Seat.LEFT] == ChangeReason.MANUAL


def test_voice_input_rejected_when_confidence_below_threshold() -> None:
    controller = ChildLockController()
    controller.complete_startup_checks()

    result = controller.handle_voice_input(
        VoiceInput(
            seat=Seat.RIGHT,
            requested_lock=True,
            confidence=0.4,
            timestamp=ts(0),
        )
    )

    assert result is None
    assert controller.status_by_seat[Seat.RIGHT].locked is False
    assert controller.diagnostics[-1].code == FaultCode.INVALID_INPUT


def test_bsd_auto_activation_is_held_for_minimum_duration() -> None:
    controller = ChildLockController(Config(auto_lock_hold_seconds=5))
    controller.complete_startup_checks()

    controller.handle_bsd_risk(seat=Seat.LEFT, risk_active=True, signal_valid=True, timestamp=ts(0))
    release_attempt = controller.handle_hmi_input(
        HmiInput(seat=Seat.LEFT, requested_lock=False, timestamp=ts(1))
    )

    assert release_attempt is None
    assert controller.status_by_seat[Seat.LEFT].locked is True
    assert controller.status_by_seat[Seat.LEFT].manual_override_blocked_until == ts(5)

    unlock = controller.handle_hmi_input(HmiInput(seat=Seat.LEFT, requested_lock=False, timestamp=ts(6)))
    assert unlock is not None
    assert controller.status_by_seat[Seat.LEFT].locked is False


def test_fire_input_has_highest_priority_and_blocks_reactivation() -> None:
    controller = ChildLockController()
    controller.complete_startup_checks()
    controller.handle_hmi_input(HmiInput(seat=Seat.BOTH, requested_lock=True, timestamp=ts(0)))

    controller.handle_fire_event(fire_active=True, signal_valid=True, timestamp=ts(1))
    relock = controller.handle_hmi_input(HmiInput(seat=Seat.LEFT, requested_lock=True, timestamp=ts(2)))

    assert relock is None
    assert controller.status_by_seat[Seat.LEFT].locked is False
    assert controller.status_by_seat[Seat.RIGHT].locked is False
    assert controller.status_view.reason_by_seat[Seat.LEFT] == ChangeReason.FIRE_RELEASE


def test_adult_detection_unlocks_only_when_confident_and_valid() -> None:
    controller = ChildLockController(Config(adult_confidence_threshold=0.8))
    controller.complete_startup_checks()
    controller.handle_hmi_input(HmiInput(seat=Seat.RIGHT, requested_lock=True, timestamp=ts(0)))

    ignored = controller.handle_adult_occupant(
        AdultOccupantInput(
            seat=Seat.RIGHT,
            adult_detected=True,
            confidence=0.7,
            valid=True,
            timestamp=ts(1),
        )
    )
    applied = controller.handle_adult_occupant(
        AdultOccupantInput(
            seat=Seat.RIGHT,
            adult_detected=True,
            confidence=0.9,
            valid=True,
            timestamp=ts(2),
        )
    )

    assert ignored is None
    assert applied is not None
    assert controller.status_by_seat[Seat.RIGHT].locked is False
    assert controller.status_view.reason_by_seat[Seat.RIGHT] == ChangeReason.ADULT_RELEASE


def test_feedback_mismatch_creates_safety_fault() -> None:
    controller = ChildLockController()
    controller.complete_startup_checks()
    controller.handle_hmi_input(HmiInput(seat=Seat.LEFT, requested_lock=True, timestamp=ts(0)))

    controller.apply_body_feedback(
        BodyFeedback(
            door_locked={Seat.LEFT: False, Seat.RIGHT: False},
            window_locked={Seat.LEFT: True, Seat.RIGHT: False},
            applied=True,
            error_code="DOOR_STUCK",
            timestamp=ts(1),
        )
    )

    assert controller.diagnostics[-1].code == FaultCode.PHYSICAL_MISMATCH
    assert controller.status_view.fault_active is True


def test_restore_policy_can_use_last_known_state() -> None:
    controller = ChildLockController(Config(power_restore_policy=PowerRestorePolicy.RESTORE_LAST))
    controller.complete_startup_checks()
    controller.handle_hmi_input(HmiInput(seat=Seat.LEFT, requested_lock=True, timestamp=ts(0)))

    restored = ChildLockController(
        Config(power_restore_policy=PowerRestorePolicy.RESTORE_LAST),
        persistence=controller.persistence,
    )

    assert restored.status_by_seat[Seat.LEFT].locked is True
    assert restored.status_by_seat[Seat.RIGHT].locked is False


def test_auto_control_is_inhibited_before_startup_checks_complete() -> None:
    controller = ChildLockController()

    result = controller.handle_bsd_risk(seat=Seat.LEFT, risk_active=True, signal_valid=True, timestamp=ts(0))

    assert result is None
    assert controller.status_by_seat[Seat.LEFT].locked is False
    assert controller.diagnostics[-1].code == FaultCode.STARTUP_INHIBIT


def test_stale_input_monitor_marks_fault_after_timeout() -> None:
    controller = ChildLockController(Config(input_timeout_seconds=2))
    controller.complete_startup_checks()
    controller.handle_bsd_risk(seat=Seat.LEFT, risk_active=True, signal_valid=True, timestamp=ts(0))

    controller.monitor_inputs(now=ts(3))

    assert controller.diagnostics[-1].code == FaultCode.INPUT_TIMEOUT

