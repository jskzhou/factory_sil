# Dual Knife Gate System Control

A Python simulation of a dual knife gate system feeding a hot kiln.

## Overview

The system controls two knife gates (A and B) that regulate widget flow into a
kiln. A chamber between the gates is flooded with CO2 to displace oxygen
before opening the kiln-side gate, preventing air from entering the kiln.

### Control Cycle

1. `load` command places a widget at the ready position
2. Gate A opens, widget transfers into the chamber
3. Gate A closes, CO2 valve opens to flood the chamber
4. Once O2 level drops to 2%, valve closes
5. Gate B opens, widget transfers into the kiln
6. Gate B closes, cycle complete, ready for next widget

### Safety

- Gates A and B are never simultaneously open
- Gate B only opens once the chamber reaches ~2% O2
- ESTOP can be issued at any time, immediately closing both gates
- All gate operations and CO2 flooding events are logged

## Project Structure

```
config.py             Constants: timing, physics, timeouts
models.py             Enums and dataclasses
logger.py             Thread-safe logger
plant.py              Plant simulation
controller.py         Controller sequencing
main.py               Entry point and CLI
```

## Running

Requires Python 3.10+ (no external dependencies). Clone this repo and run:
```bash
cd factory_sil
python main.py
```

### Commands

| Command   | Description                                          |
|-----------|------------------------------------------------------|
| `load`    | Index a widget to the ready position and start cycle |
| `estop`   | Emergency stop: closes both gates immediately        |
| `reset`   | Clear ESTOP latch (requires both gates closed)       |
| `recover` | Acknowledge manual inspection, clear stranded widget |
| `status`  | Print current plant and controller state             |
| `quit`    | Shut down and exit                                   |
| `help`    | Show available commands                              |

### Recovery After ESTOP

1. `estop`: latches ESTOP, cancels any running cycle, force-closes gates
2. `reset`: clears the ESTOP latch once gates are confirmed closed
3. `recover`: operator confirms manual inspection; clears widget state so the system can accept a new `load`

## Design Choices

### Why asyncio

This challenge was originally completed using Python `threading`, with the
controller and plant running in separate threads. The project was then
re-implemented using `asyncio`, which I believe better fits the problem while
satisfying the challenge requirements.

The plant and controller run as independent `asyncio` tasks that communicate
via queues and events, and neither can see the other's internal state directly.
Since coroutines only yield at `await` points, there are no race conditions
by default and no need for locks or mutexes. This makes safety invariants
(e.g. both gates never open simultaneously) easier to reason about and verify.
The tradeoff is that a long-running computation would block the event loop, but
all computations here are trivial so this is not a concern.

Each physical plant action (opening a gate, transferring a widget) is an async method that uses 
await asyncio.sleep(duration) to simulate the time the action takes in the real system.

```
asyncio event loop (single OS thread)
  ├── Task: plant.run_o2_sim()   - O2 concentration model
  ├── Task: controller.run()     - command dispatch loop
  │     └── Task: _run_cycle()   - linear await chain
  └── Main coroutine             - stdin via ThreadPoolExecutor
```

### Other Design Decisions

- **State entrained in program flow:** The controller cycle is a linear
  sequence of awaits (`open_gate_a`, `transfer_to_chamber`, etc.). The program
  counter tells you exactly what phase the system is in.

- **ESTOP via task cancellation:** Cancelling the cycle task raises
  `CancelledError` at the current `await`, leaving the plant in its
  transitional state. `plant.estop()` then force-closes all gates.

- **Timeouts via `asyncio.wait_for()`:** Each cycle step has a configurable
  timeout (in `config.py`). Exceeding it triggers a fault and automatic ESTOP.

- **Single background simulation task:** Only the O2 concentration model needs
  a continuous tick loop. Everything else is driven by the controller's await
  chain.

## Configuration

All timing and physics constants are in `config.py`:

| Constant                | Default | Description                          |
|-------------------------|---------|--------------------------------------|
| `SIM_DT`                | 0.05 s  | O2 simulation timestep               |
| `GATE_OPEN_TIME`        | 2.0 s   | Time for a gate to fully open        |
| `GATE_CLOSE_TIME`       | 2.0 s   | Time for a gate to fully close       |
| `CHAMBER_TRANSFER_TIME` | 2.0 s   | Conveyor time into chamber           |
| `KILN_TRANSFER_TIME`    | 2.0 s   | Conveyor time into kiln              |
| `TIMEOUT_LOADING`       | 10.0 s  | Max time for widget load             |
| `TIMEOUT_GATE_OPEN`     | 5.0 s   | Max time for gate open               |
| `TIMEOUT_GATE_CLOSE`    | 5.0 s   | Max time for gate close              |
| `TIMEOUT_TRANSFER_IN`   | 5.0 s   | Max time for chamber transfer        |
| `TIMEOUT_TRANSFER_OUT`  | 5.0 s   | Max time for kiln transfer           |
| `TIMEOUT_FLOODING`      | 60.0 s  | Max time for CO2 flooding            |
| `LOW_O2_TARGET`         | 2.0 %   | O2 level at which flooding completes |

## Logging

All gate operations, CO2 events, controller state changes, and operator
commands are logged to both stdout and a timestamped file
(`factory_log_YYYYMMDD_HHMMSS.txt`) created at startup.

## Acknowledgement of AI Use

This work benefited from the use of generative AI. The project initially
followed a different implementation approach using Python's `threading`
library, where AI assistance was limited to tab completion and routine
programming tasks. After completing that version, I concluded that `asyncio`
provided a better design fit and used agentic code generation to help convert
the implementation. The final code is therefore largely AI-generated in its
implementation, but reflects my own architectural decisions and system design.
All code and comments were reviewed and tested by the author.
