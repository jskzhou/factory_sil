"""
Async plant simulation — actuator-based model.

Each physical operation (gate open/close, conveyor transfer, CO2 flooding) is
an async method where ``await asyncio.sleep(duration)`` **is** the in-progress
state.  There is no tick loop, no timer arithmetic, and no command queue.

  * OPENING state = inside ``open_gate_a()``, suspended at the sleep.
  * CLOSING state = inside ``close_gate_a()``, suspended at the sleep.
  * MOVING_TO_CHAMBER = inside ``transfer_to_chamber()``, suspended.

ESTOP works through task cancellation: the controller cancels the running
cycle task, which raises ``CancelledError`` at the current sleep, leaving the
enum in its transitional value (e.g. OPENING).  ``estop()`` then transitions
every non-closed gate to CLOSING and sleeps for the close duration.

The only background task is the O2 concentration model (``run_o2_sim``),
which ticks at ``SIM_DT`` independently.
"""

import asyncio
import copy

import config
import models
import logger


class Plant:
    def __init__(self) -> None:
        self._state = models.PlantState()
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------ #
    # State access                                                         #
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> models.PlantState:
        """Direct reference — safe because every coroutine shares the
        event-loop thread (cooperative scheduling, no data races)."""
        return self._state

    def get_state_copy(self) -> models.PlantState:
        """Deep-copy snapshot for display."""
        return copy.deepcopy(self._state)

    def stop(self) -> None:
        self._stop_event.set()
        logger.log("plant", "stopped")

    # ------------------------------------------------------------------ #
    # Actuator methods                                                   #
    #                                                                    #
    # Each method sets the transitional enum on entry, sleeps for the    #
    # physical duration, then sets the final enum.  If the task is       #
    # cancelled mid-sleep (ESTOP), CancelledError propagates and the     #
    # enum stays at the transitional value.                              #
    # ------------------------------------------------------------------ #

    def clear_widget(self) -> None:
        """Clear widget tracking state back to NONE.

        Called after a successful cycle (widget delivered to kiln) or during
        operator recovery (widget physically removed after ESTOP).
        """
        prev = self._state.widget_location
        if prev == models.WidgetLocation.NONE:
            return
        self._state.widget_location = models.WidgetLocation.NONE
        logger.log("plant", f"widget cleared (was {prev.name})")

    async def load_widget(self) -> None:
        if self._state.widget_location != models.WidgetLocation.NONE:
            logger.log("plant", "widget loading rejected")
            return
        self._state.widget_location = models.WidgetLocation.READY
        logger.log("plant", "widget loaded")

    async def open_gate_a(self) -> None:
        if self._state.gate_b != models.GateState.CLOSED:
            logger.log("plant", "rejected OPEN_GATE_A (Gate B not closed)")
            raise models.SafetyError("Gate B not closed")
        self._state.gate_a = models.GateState.OPENING
        logger.log("plant", "Gate A opening")
        await asyncio.sleep(config.GATE_OPEN_TIME)
        self._state.gate_a = models.GateState.OPEN
        logger.log("plant", "Gate A open")

    async def close_gate_a(self) -> None:
        if self._state.gate_a == models.GateState.CLOSED:
            return
        self._state.gate_a = models.GateState.CLOSING
        logger.log("plant", "Gate A closing")
        await asyncio.sleep(config.GATE_CLOSE_TIME)
        self._state.gate_a = models.GateState.CLOSED
        logger.log("plant", "Gate A closed")

    async def open_gate_b(self) -> None:
        if self._state.gate_a != models.GateState.CLOSED:
            logger.log("plant", "rejected OPEN_GATE_B (Gate A not closed)")
            raise models.SafetyError("Gate A not closed")
        self._state.gate_b = models.GateState.OPENING
        logger.log("plant", "Gate B opening")
        await asyncio.sleep(config.GATE_OPEN_TIME)
        self._state.gate_b = models.GateState.OPEN
        logger.log("plant", "Gate B open")

    async def close_gate_b(self) -> None:
        if self._state.gate_b == models.GateState.CLOSED:
            return
        self._state.gate_b = models.GateState.CLOSING
        logger.log("plant", "Gate B closing")
        await asyncio.sleep(config.GATE_CLOSE_TIME)
        self._state.gate_b = models.GateState.CLOSED
        logger.log("plant", "Gate B closed")

    async def transfer_to_chamber(self) -> None:
        self._state.widget_location = models.WidgetLocation.MOVING_TO_CHAMBER
        logger.log("plant", "transferring widget to chamber")
        await asyncio.sleep(config.CHAMBER_TRANSFER_TIME)
        self._state.widget_location = models.WidgetLocation.IN_CHAMBER
        logger.log("plant", "widget now in chamber")

    async def transfer_to_kiln(self) -> None:
        self._state.widget_location = models.WidgetLocation.MOVING_TO_KILN
        logger.log("plant", "transferring widget to kiln")
        await asyncio.sleep(config.KILN_TRANSFER_TIME)
        self._state.widget_location = models.WidgetLocation.IN_KILN
        logger.log("plant", "widget moved to kiln")

    async def flood_co2(self) -> None:
        """Open CO2 valve and wait for O2 to drop below threshold.
        The O2 model runs in a separate background task (run_o2_sim);
        this method just polls until the threshold is reached."""
        self._state.co2_valve = models.ValveState.OPEN
        logger.log("plant", "CO2 valve opened")
        while self._state.o2_percent > config.LOW_O2_TARGET:
            await asyncio.sleep(config.SIM_DT)
        self._state.co2_valve = models.ValveState.CLOSED
        logger.log("plant", f"O2 at {self._state.o2_percent:.2f}%, CO2 valve closed")

    # ------------------------------------------------------------------ #
    # ESTOP                                                                #
    # ------------------------------------------------------------------ #
    async def estop(self) -> None:
        """Latch ESTOP, close CO2 valve, force-close all non-closed gates."""
        self._state.estop_latched = True
        self._state.co2_valve = models.ValveState.CLOSED
        logger.log("plant", "ESTOP latched, closing gates")

        closers = []
        if self._state.gate_a != models.GateState.CLOSED:
            self._state.gate_a = models.GateState.CLOSING
            closers.append(self._estop_close_gate("a"))
        if self._state.gate_b != models.GateState.CLOSED:
            self._state.gate_b = models.GateState.CLOSING
            closers.append(self._estop_close_gate("b"))
        if closers:
            await asyncio.gather(*closers)

    async def _estop_close_gate(self, gate: str) -> None:
        """Full close cycle for one gate during ESTOP."""
        await asyncio.sleep(config.GATE_CLOSE_TIME)
        if gate == "a":
            self._state.gate_a = models.GateState.CLOSED
            logger.log("plant", "Gate A closed")
        else:
            self._state.gate_b = models.GateState.CLOSED
            logger.log("plant", "Gate B closed")

    async def reset(self) -> None:
        """Clear ESTOP latch once both gates are confirmed closed."""
        if not self._state.estop_latched:
            return
        if (self._state.gate_a != models.GateState.CLOSED
                or self._state.gate_b != models.GateState.CLOSED):
            logger.log("plant", "reset rejected, gates not closed")
            return
        self._state.estop_latched = False
        self._state.fault_message = None
        logger.log("plant", "system reset accepted")

    # ------------------------------------------------------------------ #
    # O2 dynamics simulation                                             #
    # ------------------------------------------------------------------ #
    async def run_o2_sim(self) -> None:
        logger.log("plant", "started")
        while not self._stop_event.is_set():
            s = self._state

            # Gate A open -> air rushes in, O2 climbs toward ambient
            if s.gate_a == models.GateState.OPEN:
                s.o2_percent += (
                    config.K_AIR * (config.AMBIENT_O2 - s.o2_percent) * config.SIM_DT
                )

            # CO2 flooding -> O2 displaced toward 0 % (both gates must be closed)
            if (s.co2_valve == models.ValveState.OPEN
                    and s.gate_a == models.GateState.CLOSED
                    and s.gate_b == models.GateState.CLOSED):
                s.o2_percent -= config.K_CO2 * s.o2_percent * config.SIM_DT

            # Clamp O2 to physical bounds, never below 0% or above ambient
            s.o2_percent = max(0.0, min(config.AMBIENT_O2, s.o2_percent))

            await asyncio.sleep(config.SIM_DT)
