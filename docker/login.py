"""Temporary Linux desktop for the operator to log in; never part of the API."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    os.environ["DISPLAY"] = ":99"
    processes: list[subprocess.Popen] = []
    try:
        processes.append(subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1440x1000x24", "-nolisten", "tcp"]))
        for _ in range(50):
            if Path("/tmp/.X11-unix/X99").exists():
                break
            if processes[0].poll() is not None:
                raise RuntimeError("No se pudo iniciar el escritorio temporal")
            time.sleep(0.1)
        else:
            raise RuntimeError("El escritorio temporal no respondió")
        processes.append(subprocess.Popen([
            "x11vnc", "-display", ":99", "-localhost", "-nopw", "-forever", "-shared", "-rfbport", "5900",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        processes.append(subprocess.Popen([
            "websockify", "--web", "/usr/share/novnc", "0.0.0.0:6080", "127.0.0.1:5900",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        print("Abre http://127.0.0.1:6080/vnc.html mediante el túnel SSH. Completa LinkedIn y su verificación.", flush=True)
        processes.append(subprocess.Popen([sys.executable, "linkedin_jobs.py", "--login", *sys.argv[1:]]))
        return processes[-1].wait()
    except KeyboardInterrupt:
        return 130
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(143))
    raise SystemExit(main())
