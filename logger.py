"""
Thread-safe logger used by both coroutines and thread-pool workers.

Logging calls are intentionally synchronous: file writes are fast (< 1 ms)
and holding the event loop for that duration does not affect sim fidelity at
the 50 ms SIM_DT cadence.  A threading.Lock (not asyncio.Lock) is used so
that thread-pool executor threads (e.g. the stdin reader) can also log safely
without needing to interact with the event loop.
"""

import threading
from datetime import datetime

class Logger:
    def __init__(self) -> None:
        self._lock = threading.Lock()

        start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._filename = f"factory_log_{start_time}.txt"

        with open(self._filename, "w") as f:
            f.write("===== Factory Simulation Log =====\n")
            f.write(f"Start Time: {datetime.now()}\n\n")

    def log(self, source: str, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} [{source}] {message}"

        with self._lock:
            print(line)
            with open(self._filename, "a") as f:
                f.write(line + "\n")


logger = Logger()

def log(source: str, message: str) -> None:
    logger.log(source, message)