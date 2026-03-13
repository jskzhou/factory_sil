"""
Async controller — linear sequencing via await chains.

The entire widget-indexing cycle is a straight-line sequence of
``await plant.<action>()`` calls.  No polling loop, no ``_sent_*`` flags,
no timer arithmetic — the program counter **is** the state.

ESTOP handling:  the cycle runs as a background ``asyncio.Task``.  When the
operator issues ESTOP, the command dispatcher cancels that task (raising
``CancelledError`` at the current sleep inside the plant), then calls
``plant.estop()`` to force-close gates.

Timeouts use ``asyncio.wait_for()`` — a native asyncio primitive that
replaces the manual ``time.monotonic()`` bookkeeping.
"""

import asyncio
from contextlib import suppress
from typing import Optional

import config
import models
import logger

from plant import Plant


class Controller:
    def __init__(self, plant: Plant) -> None:
        self._plant = plant
        self._mode = models.ControllerState.IDLE
        self._operator_queue: asyncio.Queue[models.SystemCommand] = asyncio.Queue()
        self._cycle_task: Optional[asyncio.Task] = None
        self._fault_message: Optional[str] = None
        self._manual_recovery_required = False
        self._running = True

    # Interface
    def submit_operator_command(self, cmd: models.SystemCommand) -> None:
        self._operator_queue.put_nowait(cmd)

    def get_mode_name(self) -> str:
        return self._mode.name

    def get_fault_message(self) -> Optional[str]:
        return self._fault_message

    def is_manual_recovery_required(self) -> bool:
        return self._manual_recovery_required

    # Main command loop
    async def run(self) -> None:
        """Command dispatch loop.  The cycle runs as a concurrent task;
        this loop stays responsive to operator commands at all times."""
        logger.log("controller", "started")
        while self._running:
            cmd = await self._operator_queue.get()
            await self._dispatch(cmd)
        logger.log("controller", "stopped")

    async def _dispatch(self, cmd: models.SystemCommand) -> None:
        if cmd == models.SystemCommand.SHUTDOWN:
            await self._cancel_cycle()
            self._plant.stop()
            self._running = False

        elif cmd == models.SystemCommand.ESTOP:
            await self._do_estop()

        elif cmd == models.SystemCommand.RESET:
            await self._do_reset()

        elif cmd == models.SystemCommand.RECOVER:
            self._do_recover()

        elif cmd == models.SystemCommand.LOAD_WIDGET:
            self._do_load()

        else:
            logger.log("controller", f"ignoring unsupported command: {cmd.name}")

    # Command helper functions
    def _do_load(self) -> None:
        if self._cycle_task and not self._cycle_task.done():
            logger.log("controller", f"load rejected, controller busy in {self._mode.name}")
            return
        if self._manual_recovery_required:
            logger.log("controller", "load rejected, manual recovery required before restart")
            return
        if self._plant.state.estop_latched:
            logger.log("controller", "load rejected, ESTOP latched")
            return
        if not self._is_plant_clear():
            logger.log("controller", "load rejected, plant not in safe home condition")
            return

        logger.log("controller", "operator requested LOAD_WIDGET")
        # Fire-and-forget: the cycle task runs alongside the command loop.
        self._cycle_task = asyncio.create_task(self._run_cycle(), name="cycle")

    async def _do_estop(self) -> None:
        logger.log("controller", "operator requested ESTOP")
        await self._cancel_cycle()
        await self._plant.estop()
        self._mode = models.ControllerState.ESTOPPED
        self._manual_recovery_required = True
        self._fault_message = self._build_recovery_message()
        logger.log("controller", f"ESTOP detected: {self._fault_message}")

    async def _do_reset(self) -> None:
        logger.log("controller", "operator requested RESET")
        await self._plant.reset()
        # If the latch was successfully cleared, update controller mode.
        if not self._plant.state.estop_latched:
            if self._mode == models.ControllerState.ESTOPPED:
                if self._manual_recovery_required:
                    logger.log("controller", "ESTOP cleared, but manual recovery is still required")
                    self._mode = models.ControllerState.FAULT
                else:
                    logger.log("controller", "ESTOP cleared, returning to IDLE")
                    self._mode = models.ControllerState.IDLE

    def _do_recover(self) -> None:
        """Operator confirms manual inspection is complete.

        Requires ESTOP cleared and gates closed.  Clears any stranded widget
        state (the operator has physically removed or verified the widget)
        so the system can accept a fresh load command.
        """
        s = self._plant.state
        if s.estop_latched:
            logger.log("controller", "recovery request rejected, ESTOP still latched")
            return
        if (s.gate_a != models.GateState.CLOSED
                or s.gate_b != models.GateState.CLOSED):
            logger.log("controller", "recovery request rejected, gates not closed")
            return
        self._plant.clear_widget()
        self._manual_recovery_required = False
        self._fault_message = None
        self._mode = models.ControllerState.IDLE
        logger.log("controller", "manual recovery acknowledged, widget cleared")

    # ---------------------------------------------------------------------------------- #
    # Run full cycle as a single linear sequence of awaits, with timeouts for each step. #
    # Each await plant.<action>() blocks for the physical duration of the process        #
    # Program counter advancing through this method is the state machine                 #
    # ---------------------------------------------------------------------------------- #
    async def _run_cycle(self) -> None:
        steps = [
            (models.ControllerState.OPENING_A,    self._plant.open_gate_a,         config.TIMEOUT_GATE_OPEN),
            (models.ControllerState.INDEXING_IN,  self._plant.transfer_to_chamber, config.TIMEOUT_TRANSFER_IN),
            (models.ControllerState.CLOSING_A,    self._plant.close_gate_a,        config.TIMEOUT_GATE_CLOSE),
            (models.ControllerState.FLOODING,     self._plant.flood_co2,           config.TIMEOUT_FLOODING),
            (models.ControllerState.OPENING_B,    self._plant.open_gate_b,         config.TIMEOUT_GATE_OPEN),
            (models.ControllerState.INDEXING_OUT, self._plant.transfer_to_kiln,    config.TIMEOUT_TRANSFER_OUT),
            (models.ControllerState.CLOSING_B,    self._plant.close_gate_b,        config.TIMEOUT_GATE_CLOSE),
        ]

        try:
            # Load the widget as the first action in the cycle.
            await asyncio.wait_for(self._plant.load_widget(), timeout=config.TIMEOUT_LOADING)
            logger.log("controller", "widget ready, starting cycle")

            for mode, action, timeout in steps:
                self._mode = mode
                logger.log("controller", f"entering {mode.name}")
                await asyncio.wait_for(action(), timeout=timeout)

            self._plant.clear_widget()
            self._mode = models.ControllerState.IDLE
            logger.log("controller", "cycle complete, back to IDLE")

        except asyncio.TimeoutError:
            msg = f"timeout in {self._mode.name}"
            logger.log("controller", f"FAULT: {msg}")
            self._fault_message = msg
            self._manual_recovery_required = True
            self._mode = models.ControllerState.FAULT
            logger.log("controller", "issuing ESTOP due to fault")
            await self._plant.estop()

        except asyncio.CancelledError:
            # ESTOP/shutdown cancelled this task; caller will handle the cleanup.
            raise

    async def _cancel_cycle(self) -> None:
        if self._cycle_task and not self._cycle_task.done():
            self._cycle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cycle_task
            self._cycle_task = None

    def _is_plant_clear(self) -> bool:
        s = self._plant.state
        return (
            s.gate_a == models.GateState.CLOSED
            and s.gate_b == models.GateState.CLOSED
            and s.co2_valve == models.ValveState.CLOSED
            and s.widget_location == models.WidgetLocation.NONE
        )

    def _build_recovery_message(self) -> str:
        loc = self._plant.state.widget_location
        messages = {
            models.WidgetLocation.IN_CHAMBER:
                "widget remains in chamber after ESTOP; manual inspection required before reset",
            models.WidgetLocation.MOVING_TO_CHAMBER:
                "ESTOP during transfer to chamber; widget position uncertain, manual inspection required",
            models.WidgetLocation.MOVING_TO_KILN:
                "ESTOP during transfer to kiln; widget position uncertain, manual inspection required",
            models.WidgetLocation.IN_KILN:
                "ESTOP occurred after transfer to kiln; verify downstream state before restart",
            models.WidgetLocation.READY:
                "ESTOP with widget at load position; verify system state before restart",
        }
        return messages.get(loc, "ESTOP occurred; manual inspection required before restart")
