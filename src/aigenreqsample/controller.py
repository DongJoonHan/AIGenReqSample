from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import (
    AdultOccupantInput,
    BodyCommand,
    BodyFeedback,
    BodyResult,
    ChangeReason,
    Config,
    DiagnosticEvent,
    FaultCode,
    HmiInput,
    PowerRestorePolicy,
    Seat,
    SeatState,
    StatusView,
    VoiceInput,
)


LEFT_RIGHT_SEATS = (Seat.LEFT, Seat.RIGHT)


@dataclass
class MemoryPersistence:
    last_locked_by_seat: dict[Seat, bool] | None = None

    def load(self) -> dict[Seat, bool] | None:
        return self.last_locked_by_seat

    def save(self, locked_by_seat: dict[Seat, bool]) -> None:
        self.last_locked_by_seat = dict(locked_by_seat)


class ChildLockController:
    def __init__(self, config: Config | None = None, persistence: MemoryPersistence | None = None) -> None:
        self.config = config or Config()
        self.persistence = persistence or MemoryPersistence()
        self.startup_checks_complete = False
        self.fire_active = False
        self.last_body_command: BodyCommand | None = None
        self.body_sequence = 0
        self.diagnostics: list[DiagnosticEvent] = []
        self.last_input_seen: dict[str, datetime] = {}
        self.status_by_seat = self._build_initial_state()
        self.status_view = self._make_status_view()

    def complete_startup_checks(self) -> None:
        self.startup_checks_complete = True

    def handle_hmi_input(self, event: HmiInput) -> BodyResult | None:
        if not self._manual_allowed(event.timestamp, event.seat, event.requested_lock):
            return None
        return self._apply_request(event.seat, event.requested_lock, ChangeReason.MANUAL, event.timestamp)

    def handle_voice_input(self, event: VoiceInput) -> BodyResult | None:
        if event.confidence < self.config.voice_confidence_threshold:
            self._fault(FaultCode.INVALID_INPUT, event.seat, event.timestamp, "Voice confidence below threshold")
            return None
        if not self._manual_allowed(event.timestamp, event.seat, event.requested_lock):
            return None
        return self._apply_request(event.seat, event.requested_lock, ChangeReason.VOICE, event.timestamp)

    def handle_bsd_risk(
        self,
        seat: Seat,
        risk_active: bool,
        signal_valid: bool,
        timestamp: datetime,
    ) -> BodyResult | None:
        self.last_input_seen["bsd"] = timestamp
        if not self.startup_checks_complete:
            self._fault(FaultCode.STARTUP_INHIBIT, seat, timestamp, "Automatic control inhibited during startup")
            return None
        if not signal_valid:
            self._fault(FaultCode.INVALID_INPUT, seat, timestamp, "Invalid BSD signal")
            return None
        if not risk_active:
            return None
        result = self._apply_request(seat, True, ChangeReason.BSD_AUTO_LOCK, timestamp)
        block_until = timestamp + timedelta(seconds=self.config.auto_lock_hold_seconds)
        for target in self._expand_seat(seat):
            self.status_by_seat[target].manual_override_blocked_until = block_until
        self.status_view = self._make_status_view()
        return result

    def handle_fire_event(self, fire_active: bool, signal_valid: bool, timestamp: datetime) -> BodyResult | None:
        self.last_input_seen["fire"] = timestamp
        if not signal_valid:
            self._fault(FaultCode.INVALID_INPUT, None, timestamp, "Invalid fire signal")
            return None
        self.fire_active = fire_active
        if not fire_active:
            return None
        return self._apply_request(Seat.BOTH, False, ChangeReason.FIRE_RELEASE, timestamp)

    def handle_adult_occupant(self, event: AdultOccupantInput) -> BodyResult | None:
        self.last_input_seen["occupant"] = event.timestamp
        if not self.startup_checks_complete:
            self._fault(FaultCode.STARTUP_INHIBIT, event.seat, event.timestamp, "Automatic control inhibited during startup")
            return None
        if not event.valid:
            self._fault(FaultCode.INVALID_INPUT, event.seat, event.timestamp, "Invalid occupant input")
            return None
        if not event.adult_detected or event.confidence < self.config.adult_confidence_threshold:
            return None
        return self._apply_request(event.seat, False, ChangeReason.ADULT_RELEASE, event.timestamp)

    def apply_body_feedback(self, feedback: BodyFeedback) -> None:
        if not feedback.applied:
            self._fault(FaultCode.COMMAND_FAILED, None, feedback.timestamp, feedback.error_code or "Command failed")
        for seat in LEFT_RIGHT_SEATS:
            logical = self.status_by_seat[seat].locked
            physical_ok = feedback.door_locked[seat] == logical and feedback.window_locked[seat] == logical
            if not physical_ok:
                self._fault(FaultCode.PHYSICAL_MISMATCH, seat, feedback.timestamp, feedback.error_code or "Physical mismatch")
        self.status_view = self._make_status_view()

    def monitor_inputs(self, now: datetime) -> None:
        timeout = timedelta(seconds=self.config.input_timeout_seconds)
        for channel, seen_at in self.last_input_seen.items():
            if now - seen_at > timeout:
                self._fault(FaultCode.INPUT_TIMEOUT, None, now, f"{channel} input timeout")

    def _build_initial_state(self) -> dict[Seat, SeatState]:
        locked = {seat: False for seat in LEFT_RIGHT_SEATS}
        if self.config.power_restore_policy is PowerRestorePolicy.RESTORE_LAST:
            restored = self.persistence.load()
            if restored:
                locked.update({seat: bool(restored.get(seat, False)) for seat in LEFT_RIGHT_SEATS})
        reason = ChangeReason.RESTORED if any(locked.values()) else ChangeReason.DEFAULT
        return {
            seat: SeatState(locked=locked[seat], reason=reason)
            for seat in LEFT_RIGHT_SEATS
        }

    def _manual_allowed(self, now: datetime, seat: Seat, requested_lock: bool) -> bool:
        if self.fire_active and requested_lock:
            return False
        for target in self._expand_seat(seat):
            blocked_until = self.status_by_seat[target].manual_override_blocked_until
            if blocked_until and now < blocked_until and not requested_lock:
                return False
        return True

    def _apply_request(
        self,
        seat: Seat,
        requested_lock: bool,
        reason: ChangeReason,
        timestamp: datetime,
    ) -> BodyResult:
        changed_seats: set[Seat] = set()
        for target in self._expand_seat(seat):
            state = self.status_by_seat[target]
            if state.locked != requested_lock or state.reason != reason:
                state.locked = requested_lock
                state.reason = reason
                state.changed_at = timestamp
                if reason is ChangeReason.FIRE_RELEASE:
                    state.manual_override_blocked_until = None
                changed_seats.add(target)

        self.body_sequence += 1
        locked_map = {target: self.status_by_seat[target].locked for target in LEFT_RIGHT_SEATS}
        self.last_body_command = BodyCommand(
            door_locked=locked_map.copy(),
            window_locked=locked_map.copy(),
            sequence=self.body_sequence,
            timestamp=timestamp,
        )
        self.persistence.save(locked_map)
        self.status_view = self._make_status_view()
        return BodyResult(
            command=self.last_body_command,
            status_view=self.status_view,
            changed_seats=changed_seats,
        )

    def _expand_seat(self, seat: Seat) -> tuple[Seat, ...]:
        if seat is Seat.BOTH:
            return LEFT_RIGHT_SEATS
        return (seat,)

    def _fault(self, code: FaultCode, seat: Seat | None, timestamp: datetime, message: str) -> None:
        self.diagnostics.append(DiagnosticEvent(code=code, seat=seat, timestamp=timestamp, message=message))
        self.status_view = self._make_status_view()

    def _make_status_view(self) -> StatusView:
        return StatusView(
            locked_by_seat={seat: self.status_by_seat[seat].locked for seat in LEFT_RIGHT_SEATS},
            reason_by_seat={seat: self.status_by_seat[seat].reason for seat in LEFT_RIGHT_SEATS},
            automatic_by_seat={
                seat: self.status_by_seat[seat].reason
                in {
                    ChangeReason.BSD_AUTO_LOCK,
                    ChangeReason.FIRE_RELEASE,
                    ChangeReason.ADULT_RELEASE,
                    ChangeReason.RESTORED,
                }
                for seat in LEFT_RIGHT_SEATS
            },
            fault_active=bool(self.diagnostics),
        )
