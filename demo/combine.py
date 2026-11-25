"""Join recorded casts into one, optionally dropping whole commands. Output is never edited.

    python3 demo/combine.py out.cast part1.cast part2a.cast:-gateway part2b.cast

`file.cast:-WORD` drops every command whose typed line contains WORD, together with its output, cut
at the prompts before and after it. Everything kept plays at its recorded pace; each part starts where
the previous one ended.
"""

from __future__ import annotations

import json
import re
import sys

PROMPT_RE = re.compile(r"\$\x1b\[0m $")


def read(path: str) -> tuple[dict, list[list]]:
    lines = open(path).read().splitlines()
    return json.loads(lines[0]), [json.loads(l) for l in lines[1:]]


def segments(events: list[list]) -> list[list[list]]:
    """Split at each prompt: segment k is prompt k's typed command plus its output."""
    segs, cur = [], []
    for e in events:
        cur.append(e)
        if PROMPT_RE.search(e[2]):
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    return segs


def typed(seg: list[list]) -> str:
    text = "".join(e[2] for e in seg)
    return re.sub(r"\x1b\[[0-9;]*m", "", text.split("\r\n", 1)[0])


def main() -> int:
    out, parts = sys.argv[1], sys.argv[2:]
    header, merged, t = None, [], 0.0
    for spec in parts:
        path, _, drop = spec.partition(":-")
        h, events = read(path)
        header = header or h
        segs = segments(events)
        kept = [s for i, s in enumerate(segs) if not (drop and i > 0 and drop in typed(s))]
        if merged:  # the previous part ended on a prompt; this part's opening prompt would repeat it
            kept = kept[1:]
        for seg in kept:
            prev = None
            for e in seg:
                t += 0.3 if prev is None else max(e[0] - prev, 0.0)  # recorded pace inside a command
                prev = e[0]
                merged.append([round(t, 3), e[1], e[2]])
    with open(out, "w") as f:
        f.write(json.dumps(header) + "\n")
        for e in merged:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"{out}: {len(merged)} events, {merged[-1][0]:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
