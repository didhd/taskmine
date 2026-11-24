"""Record a real terminal session as an asciicast v2 file (asciinema's format), standard library only.

    python3 demo/record.py demo/part1.txt out.cast [--cols 110 --rows 30 --idle 1.5]

The script file holds one shell command per line (`#` lines are shown as comments). Each command is
typed at a human pace, then RUN for real in a pseudo-terminal; its output is recorded with the times
it actually arrived. Only waiting can be shortened: with --idle N a gap longer than N seconds is cut to N; --idle 0
keeps every interval as it happened.
Nothing in the output is edited or invented.
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
    a = ap.parse_args()

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


if __name__ == "__main__":
    sys.exit(main())
