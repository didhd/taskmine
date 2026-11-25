"""Record a real terminal session as an asciicast v2 file (asciinema's format), standard library only.

    python3 demo/record.py SCRIPT out.cast --live [--cols 110 --rows 30 --pause 1.0]
    python3 demo/record.py SCRIPT out.cast [--idle 1.5]          (the older, typed style)

The script file holds one shell command per line (`#` lines are shown as comments). Each command is
RUN for real in a pseudo-terminal, and its output is recorded with the times it actually arrived.
Nothing in the output is edited or invented.

--live: every event's time is the wall clock since the recording started. A command appears the
  moment it starts; between commands the recorder really waits --pause seconds. The cast's length
  is the session's length.
Without --live (the older style): commands are shown typed at 35 ms a character, with short fixed
  pauses, and --idle N cuts waits longer than N seconds to N (--idle 0 keeps them).
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import select
import subprocess
import sys
import time

PROMPT = "\x1b[1;36m~/shop\x1b[0m \x1b[1;35m$\x1b[0m "


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("cast")
    ap.add_argument("--cols", type=int, default=110)
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--idle", type=float, default=1.5)
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--title", default="taskmine")
    ap.add_argument("--live", action="store_true", help="wall-clock times for everything; no typing")
    ap.add_argument("--pause", type=float, default=1.0, help="--live: seconds really waited between commands")
    a = ap.parse_args()
    if a.live:
        return live(a)

    events: list[tuple[float, str]] = []
    clock = {"t": 0.0, "last": None}

    def emit(text: str, dt: float = 0.0) -> None:
        clock["t"] += dt
        events.append((round(clock["t"], 3), text))

    env = dict(os.environ, COLUMNS=str(a.cols), LINES=str(a.rows), TERM="xterm-256color", TASKMINE_COLOR="1")
    lines = [l.rstrip("\n") for l in open(a.script) if l.strip()]
    emit(a.prompt, 0.3)
    for line in lines:
        if line.startswith("#"):
            for ch in line:
                emit("\x1b[2m" + ch + "\x1b[0m", 0.018)
            emit("\r\n" + a.prompt, 0.4)
            continue
        if line.startswith("@sleep "):
            clock["t"] += float(line.split()[1])
            continue
        for ch in line:
            emit(ch, 0.035)
        emit("\r\n", 0.35)
        master, slave = pty.openpty()
        os.set_blocking(master, False)
        import fcntl, struct, termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", a.rows, a.cols, 0, 0))
        p = subprocess.Popen(["bash", "-c", line], stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
        os.close(slave)
        last = time.time()
        while True:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if data:
                    now = time.time()
                    emit(data.decode("utf-8", "replace"), (now - last) if a.idle <= 0 else min(now - last, a.idle))
                    last = now
                    continue
            if p.poll() is not None:
                try:
                    while True:
                        data = os.read(master, 65536)
                        if not data:
                            break
                        emit(data.decode("utf-8", "replace"), 0.01)
                except OSError:
                    pass
                break
        os.close(master)
        emit(a.prompt, 0.5)
    emit("", 2.0)
    with open(a.cast, "w") as f:
        f.write(json.dumps({"version": 2, "width": a.cols, "height": a.rows, "timestamp": int(time.time()),
                            "title": a.title, "env": {"TERM": "xterm-256color"}}) + "\n")
        for t, text in events:
            f.write(json.dumps([t, "o", text], ensure_ascii=False) + "\n")
    print(f"{a.cast}: {len(events)} events, {events[-1][0]:.1f} s", file=sys.stderr)
    return 0


def live(a) -> int:
    """Real time end to end: every event is stamped with the wall clock when it was written."""
    import fcntl
    import struct
    import termios
    start = time.time()
    events: list[tuple[float, str]] = []

    def emit(text: str) -> None:
        events.append((round(time.time() - start, 3), text))

    env = dict(os.environ, COLUMNS=str(a.cols), LINES=str(a.rows), TERM="xterm-256color", TASKMINE_COLOR="1")
    lines = [l.rstrip("\n") for l in open(a.script) if l.strip()]
    emit(a.prompt)
    for i, line in enumerate(lines):
        if i:
            time.sleep(a.pause)
        if line.startswith("#"):
            emit("\x1b[2m" + line + "\x1b[0m\r\n" + a.prompt)
            continue
        emit(line + "\r\n")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", a.rows, a.cols, 0, 0))
        p = subprocess.Popen(["bash", "-c", line], stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
        os.close(slave)
        while True:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if data:
                    emit(data.decode("utf-8", "replace"))
                    continue
            if p.poll() is not None:
                try:
                    while data := os.read(master, 65536):
                        emit(data.decode("utf-8", "replace"))
                except OSError:
                    pass
                break
        os.close(master)
        emit(a.prompt)
    time.sleep(a.pause)
    emit("")
    with open(a.cast, "w") as f:
        f.write(json.dumps({"version": 2, "width": a.cols, "height": a.rows, "timestamp": int(start),
                            "title": a.title, "env": {"TERM": "xterm-256color"}}) + "\n")
        for t, text in events:
            f.write(json.dumps([t, "o", text], ensure_ascii=False) + "\n")
    print(f"{a.cast}: {len(events)} events, {events[-1][0]:.1f} s of wall clock", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
