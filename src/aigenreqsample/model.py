from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Seat(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"


class ChangeReason(str, Enum):
    MANUAL = "manual"
    VOICE = "voice"
    BSD_AUTO_LOCK = "bsd_auto_lock"
    FIRE_RELEASE = "fire_release"
    ADULT_RELEASE = "adult_release"
    RESTORED = "restored"
    DEFAULT = "default"


class FaultCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    INPUT_TIMEOUT = "input_timeout"
    COMMAND_FAILED = "command_failed"
    PHYSICAL_MISMATCH = "physical_mismatch"
    STARTUP_INHIBIT = "startup_inhibit"


class PowerRestorePolicy(str, Enum):
    DEFAULT_UNLOCKED = "default_unlocked"
    RESTORE_LAST = "restore_last"


@dataclass(slots=True)
class Config:
    voice_confidence_threshold: float = 0.6
    adult_confidence_threshold: float = 0.75
    auto_lock_hold_seconds: int = 3
    input_timeout_seconds: int = 1
    power_restore_policy: PowerRestorePolicy = PowerRestorePolicy.DEFAULT_UNLOCKED


@dataclass(slots=True)
class SeatState:
    locked: bool = False
    reason: ChangeReason = ChangeReason.DEFAULT
    changed_at: datetime | None = None
    manual_override_blocked_until: datetime | None = None


@dataclass(slots=True)
class HmiInput:
    seat: Seat
    requested_lock: bool
    timestamp: datetime
    source: str = "hmi"


@dataclass(slots=True)
class VoiceInput:
    seat: Seat
    requested_lock: bool
    confidence: float
    timestamp: datetime


@dataclass(slots=True)
class AdultOccupantInput:
    seat: Seat
    adult_detected: bool
    confidence: float
    valid: bool
    timestamp: datetime


@dataclass(slots=True)
class BodyCommand:
    door_locked: dict[Seat, bool]
    window_locked: dict[Seat, bool]
    sequence: int
    timestamp: datetime


@dataclass(slots=True)
class BodyFeedback:
    door_locked: dict[Seat, bool]
    window_locked: dict[Seat, bool]
    applied: bool
    error_code: str | None
    timestamp: datetime


@dataclass(slots=True)
class StatusView:
    locked_by_seat: dict[Seat, bool]
    reason_by_seat: dict[Seat, ChangeReason]
    automatic_by_seat: dict[Seat, bool]
    fault_active: bool


@dataclass(slots=True)
class DiagnosticEvent:
    code: FaultCode
    seat: Seat | None
    timestamp: datetime
    message: str


@dataclass(slots=True)
class BodyResult:
    command: BodyCommand
    status_view: StatusView
    changed_seats: set[Seat] = field(default_factory=set)
