from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional

import config


class GateState(Enum):
    OPENING = auto()
    CLOSING = auto()
    OPEN    = auto()
    CLOSED  = auto()


class ValveState(Enum):
    OPEN   = auto()
    CLOSED = auto()


class WidgetLocation(Enum):
    NONE              = auto()
    READY             = auto()
    MOVING_TO_CHAMBER = auto()
    IN_CHAMBER        = auto()
    MOVING_TO_KILN    = auto()
    IN_KILN           = auto()


class ControllerState(Enum):
    """Tracks which phase the controller is in for status display only.
    The actual sequencing is entrained in the controller coroutine's await chain."""
    IDLE        = auto()
    OPENING_A   = auto()
    INDEXING_IN = auto()
    CLOSING_A   = auto()
    FLOODING    = auto()
    OPENING_B   = auto()
    INDEXING_OUT = auto()
    CLOSING_B   = auto()
    ESTOPPED    = auto()
    FAULT       = auto()


class SystemCommand(Enum):
    """Operator-level commands"""
    LOAD_WIDGET = auto()
    ESTOP       = auto()
    RESET       = auto()
    RECOVER     = auto()
    SHUTDOWN    = auto()


class SafetyError(Exception):
    """Raised when a plant operation would violate a safety invariant."""


@dataclass
class PlantState:
    """Default Plant state"""
    gate_a:          GateState      = GateState.CLOSED
    gate_b:          GateState      = GateState.CLOSED
    co2_valve:       ValveState     = ValveState.CLOSED
    widget_location: WidgetLocation = WidgetLocation.NONE
    o2_percent:      float          = config.AMBIENT_O2
    estop_latched:   bool           = False
    fault_message:   Optional[str]  = None
