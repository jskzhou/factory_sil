"""
Async entry point for the factory controller simulation.

Concurrency model
-----------------
asyncio event loop (single OS thread)
  ├── Task: plant.run_o2_sim()   — background O2 concentration model
  ├── Task: controller.run()     — command dispatch + cycle sequencing
  └── Main coroutine             — user input via ThreadPoolExecutor

Usage
-----
    python main.py
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import models
from plant import Plant
from controller import Controller

def print_help() -> None:
    print()
    print("Commands:")
    print("  help      - show this help")
    print("  status    - print current plant and controller state")
    print("  load      - load a widget into ready position")
    print("  estop     - emergency stop")
    print("  reset     - clear estop latch once gates are closed")
    print("  recover   - operator acknowledges manual inspection / recovery")
    print("  quit      - stop controller and plant, then exit")
    print()


def print_status(plant: Plant, controller: Controller) -> None:
    state = plant.get_state_copy()
    print()
    print("Controller status:")
    print(f"  Mode:              {controller.get_mode_name()}")
    print(f"  Fault:             {controller.get_fault_message()}")
    print(f"  Recovery Required: {controller.is_manual_recovery_required()}")
    print()
    print("Plant status:")
    print(f"  Gate A:            {state.gate_a.name}")
    print(f"  Gate B:            {state.gate_b.name}")
    print(f"  CO2 Valve:         {state.co2_valve.name}")
    print(f"  Widget:            {state.widget_location.name}")
    print(f"  O2:                {state.o2_percent:.2f}%")
    print(f"  ESTOP:             {state.estop_latched}")
    print(f"  Plant Fault:       {state.fault_message}")
    print()


async def main() -> None:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stdin")

    plant = Plant()
    controller = Controller(plant)

    o2_task   = asyncio.create_task(plant.run_o2_sim(), name="o2_sim")
    ctrl_task = asyncio.create_task(controller.run(), name="controller")

    loop = asyncio.get_running_loop()

    print("Factory Controller Simulation (async)")
    print_help()

    try:
        while True:
            user_input = await loop.run_in_executor(executor, input, ">> ")
            user_input = user_input.strip().lower()

            if not user_input:
                continue

            if user_input == "help":
                print_help()
            elif user_input == "status":
                print_status(plant, controller)
            elif user_input == "load":
                controller.submit_operator_command(models.SystemCommand.LOAD_WIDGET)
            elif user_input == "estop":
                controller.submit_operator_command(models.SystemCommand.ESTOP)
            elif user_input == "reset":
                controller.submit_operator_command(models.SystemCommand.RESET)
            elif user_input == "recover":
                controller.submit_operator_command(models.SystemCommand.RECOVER)
            elif user_input == "quit":
                controller.submit_operator_command(models.SystemCommand.SHUTDOWN)
                break
            else:
                print("Unknown command. Type 'help' for options.")

    except (KeyboardInterrupt, EOFError):
        print("\nShutdown requested, stopping.")
        controller.submit_operator_command(models.SystemCommand.SHUTDOWN)

    # Controller's SHUTDOWN handler calls plant.stop(), which sets the
    # stop_event so the O2 sim exits naturally.
    await ctrl_task
    with suppress(asyncio.CancelledError):
        await o2_task

    executor.shutdown(wait=False)
    print("Exited cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
