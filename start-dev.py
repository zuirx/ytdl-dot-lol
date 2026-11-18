"""
start-dev.py: Launcher for local development environment.

Runs:
  1. Redis on WSL Debian:   wsl -d debian redis-server
  2. Celery Worker:         python -m celery -A ytdl worker --pool=solo
  3. Celery Beat:           python -m celery -A ytdl beat -l info
  4. Django Dev Server:     python runserver-dev.py

Usage:
  python start-dev.py            # Opens all 4 services in separate labeled windows
  python start-dev.py --unified  # Runs all 4 services in one terminal with graceful shutdown
"""

import sys
import os
import subprocess
import time
import signal
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICES = [
    {
        "name": "Redis (WSL Debian)",
        "command": ["wsl", "-d", "debian", "redis-server"],
        "color": "\033[91m",  # Red
    },
    {
        "name": "Celery Worker",
        "command": [sys.executable, "-m", "celery", "-A", "ytdl", "worker", "--pool=solo"],
        "color": "\033[92m",  # Green
    },
    {
        "name": "Celery Beat",
        "command": [sys.executable, "-m", "celery", "-A", "ytdl", "beat", "-l", "info"],
        "color": "\033[95m",  # Magenta
    },
    {
        "name": "Django Server",
        "command": [sys.executable, os.path.join(BASE_DIR, "runserver-dev.py")],
        "color": "\033[96m",  # Cyan
    },
]

RESET_COLOR = "\033[0m"


def launch_separate_windows():
    print("=" * 60)
    print("  Starting YTDL Services in Separate Windows...")
    print("=" * 60)

    for idx, s in enumerate(SERVICES, start=1):
        name = s["name"]
        cmd = s["command"]
        print(f"[{idx}/4] Launching {name}...")

        if sys.platform == "win32":
            # Use 'cmd /k' so window stays open if command errors
            full_cmd = ["cmd", "/k", f"title {name} && " + " ".join(cmd)]
            subprocess.Popen(
                full_cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=BASE_DIR,
            )
        else:
            subprocess.Popen(cmd, cwd=BASE_DIR)

        time.sleep(1.5)

    print("\n" + "=" * 60)
    print("  All 4 services launched successfully!")
    print("  Django is accessible at: http://127.0.0.1:8000")
    print("=" * 60)


def stream_output(pipe, prefix, color):
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            print(f"{color}[{prefix}]{RESET_COLOR} {line.rstrip()}")
    except (ValueError, OSError):
        pass


def launch_unified():
    print("=" * 60)
    print("  Starting YTDL Services in Unified Mode (Press Ctrl+C to Stop)...")
    print("=" * 60)

    processes = []

    def cleanup(sig=None, frame=None):
        print("\nStopping all services...")
        for p, s in processes:
            try:
                if sys.platform == "win32":
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    for s in SERVICES:
        name = s["name"]
        cmd = s["command"]
        color = s["color"]
        print(f"Starting {name}...")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
        )
        processes.append((proc, s))

        thread = threading.Thread(target=stream_output, args=(proc.stdout, name, color), daemon=True)
        thread.start()
        time.sleep(1.5)

    print("\n" + "=" * 60)
    print("  All services running! Dev server at http://127.0.0.1:8000")
    print("  Press Ctrl+C to stop all services simultaneously.")
    print("=" * 60 + "\n")

    try:
        while True:
            time.sleep(1)
            for p, s in processes:
                if p.poll() is not None:
                    print(f"\n{s['name']} exited with return code {p.returncode}")
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    if "--unified" in sys.argv:
        launch_unified()
    else:
        launch_separate_windows()
