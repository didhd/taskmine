"""taskmine explainer video, built with the video-generator plugin's pieces.

Uses from video-render/server.py: the AWS 2026 dark slide theme and logo (_build_slide_html),
Amazon Polly TTS (generate_tts), audio probing (_get_duration) and Nova 2 Lite QA (video_qa).
Slides are rendered at 2x (3840x2160) and downscaled, so text is crisp; each scene reveals in
steps that cross-fade, and scenes cross-fade into each other. No generated imagery: every frame is
text, code or data drawn from real runs.

  python make_video.py frames [scene ...]   render PNGs only (fast look)
  python make_video.py tts                  narration per scene (Polly)
  python make_video.py video                compose the MP4 (+ poster + GIF)
  python make_video.py qa                   Nova 2 Lite QA on the MP4
"""

from __future__ import annotations

import asyncio
import html
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "video-render"))
import server  # noqa: E402  (the plugin)

OUT = HERE / "build"
NUM = json.loads((HERE / "numbers.json").read_text())
FF = "/opt/homebrew/bin/ffmpeg"
W, H, FPS = 1920, 1080, 30
X = 0.5  # scene cross-fade, seconds
STEP_X = 0.35  # cross-fade between the build steps of one scene

ORANGE, BLUE, GREEN, PURPLE, RED = "#FF6200", "#0578FF", "#6EE750", "#B052FF", "#FF5C5C"
INK2, MUTED = "#161D26", "#9AA4B2"
MONO = "'JetBrains Mono', 'SF Mono', Menlo, monospace"

CSS = f"""
<style>
.k {{ color:{ORANGE}; }} .muted {{ color:{MUTED}; }} .mono {{ font-family:{MONO}; }}
.lead {{ font-size:36px; line-height:1.45; max-width:1450px; }}
.row {{ display:flex; gap:28px; align-items:stretch; }}
.card {{ background:{INK2}; border-radius:18px; padding:30px 34px; flex:1; border:1px solid #2E3A4A; }}
.card h3 {{ font-size:30px; margin-bottom:12px; font-weight:700; }}
.card p {{ font-size:25px; line-height:1.45; color:#D5DBE3; }}
.dim {{ opacity:.18; }}
.chip {{ display:inline-block; font-family:{MONO}; font-size:22px; padding:6px 14px; border-radius:10px;
         border:1px solid #3A4757; color:#D5DBE3; margin-right:10px; }}
.arrow {{ align-self:center; font-size:46px; color:{ORANGE}; }}
.runs {{ display:flex; gap:18px; }}
.run {{ flex:1; background:{INK2}; border-radius:16px; padding:26px 18px; text-align:center; border:1px solid #2E3A4A; }}
.run .name {{ font-family:{MONO}; font-size:24px; color:#D5DBE3; }}
.run .mark {{ font-size:60px; font-weight:800; margin:10px 0 6px; }}
.run .what {{ font-size:20px; color:{MUTED}; line-height:1.35; }}
pre.code {{ font-family:{MONO}; font-size:23px; line-height:1.55; background:{INK2}; border-radius:16px;
            padding:28px 32px; white-space:pre; border:1px solid #2E3A4A; color:#E6EAF0; }}
.bar {{ display:flex; align-items:center; gap:22px; margin:12px 0; }}
.bar .lab {{ width:330px; font-size:26px; color:#D5DBE3; text-align:right; }}
.bar .fill {{ height:46px; border-radius:8px; }}
.bar .val {{ font-family:{MONO}; font-size:28px; font-weight:700; }}
.small {{ font-size:22px; color:{MUTED}; line-height:1.5; }}
.term {{ font-family:{MONO}; font-size:25px; line-height:1.65; background:#0F141B; border-radius:16px;
         padding:30px 36px; border:1px solid #2E3A4A; white-space:pre; color:#E6EAF0; }}
.big {{ font-family:'Ember Modern Display Medium','Amazon Ember',sans-serif; font-size:120px; font-weight:700;
        letter-spacing:-2px; }}
</style>
"""


def e(s: str) -> str:
    return html.escape(s)


def run_box(name: str, mark: str, color: str, what: str, dim: bool) -> str:
    return (f'<div class="run{" dim" if dim else ""}"><div class="name">{e(name)}</div>'
            f'<div class="mark" style="color:{color}">{mark}</div><div class="what">{what}</div></div>')


def funnel(step: int) -> str:
    rows = [
        ("merges since June", NUM["units"], "#D5DBE3"),
        ("pull requests that change code and tests", NUM["candidates"], BLUE),
        ("tasks proven by execution", NUM["accepted"], ORANGE),
    ]
    cards = []
    for i, (lab, val, col) in enumerate(rows):
        cards.append(f'<div class="card{"" if i <= step else " dim"}" style="text-align:center;padding:44px 20px">'
                     f'<div class="mono" style="font-size:92px;font-weight:800;color:{col}">{val:,}</div>'
                     f'<p style="margin-top:10px">{e(lab)}</p></div>')
        if i < len(rows) - 1:
            cards.append(f'<div class="arrow{"" if i < step else " dim"}">&#8594;</div>')
    return f'<div class="row">{"".join(cards)}</div>'


def scenes() -> list[dict]:
    S = []
    S.append(dict(id="s01", title="", steps=[
        f'{CSS}<div class="big">task<span class="k">mine</span></div>'
        f'<div class="lead">Your merged pull requests, turned into a Terminal-Bench task set where every task '
        f'is proven by execution.</div>'
        f'<div style="margin-top:18px"><span class="chip">Harbor / Terminal-Bench 2.x tasks</span>'
        f'<span class="chip">Python standard library</span><span class="chip">any Docker host</span></div>'],
        text="taskmine turns the pull requests your team already merged into a Terminal-Bench task set. "
             "It keeps only the tasks that execution proves."))
    S.append(dict(id="s02", title="Benchmarks on your own code", steps=[
        f'{CSS}<div class="row"><div class="card"><h3 class="muted">Public benchmark</h3>'
        f'<p>Someone else\'s repositories, tasks and graders. It ranks harnesses, but not on your code.</p></div>'
        f'<div class="arrow">&#8594;</div><div class="card dim"><h3>Your repository</h3><p>&nbsp;</p></div></div>',
        f'{CSS}<div class="row"><div class="card"><h3 class="muted">Public benchmark</h3>'
        f'<p>Someone else\'s repositories, tasks and graders. It ranks harnesses, but not on your code.</p></div>'
        f'<div class="arrow">&#8594;</div><div class="card" style="border-color:{ORANGE}"><h3>Your repository</h3>'
        f'<p>Tasks and graders mined from your own history. Same harnesses, same models, your code.</p></div></div>'],
        text="Public benchmarks measure agents on someone else's code. To compare models and harnesses on yours, "
             "you need tasks and graders that come from your own history, with the answer kept out of sight."))
    stages = [("scan", "merged changes that touch code and tests"),
              ("build", "seal the base commit, install its dependencies, keep tests and patch apart"),
              ("validate", "run everything in Docker, with no network"),
              ("finalize", "write the instruction and task.toml")]
    steps = []
    for k in range(len(stages)):
        cards = "".join(
            f'<div class="card{"" if i <= k else " dim"}"><h3><span class="k mono">{i + 1}</span>&nbsp; {e(n)}</h3>'
            f'<p>{e(d)}</p></div>' for i, (n, d) in enumerate(stages))
        steps.append(f'{CSS}<div class="row">{cards}</div>')
    S.append(dict(id="s03", title="Four commands", steps=steps,
                  text="Scan finds merged changes that touch both code and tests. Build seals the repository at the "
                       "base commit, installs its dependencies, and keeps the tests and the merged patch apart. "
                       "Validate runs every task in Docker. Finalize writes it out in Harbor's format."))
    runs = [("base x2", "&#10007;", RED, "new tests fail<br>without the change"),
            ("gold x3", "&#10003;", GREEN, "they pass<br>with it"),
            ("text probe", "&#10003;", GREEN, "they pass on<br>behaviour, not text"),
            ("nop", "0", MUTED, "do-nothing agent<br>scores zero"),
            ("oracle", "1", ORANGE, "the merged patch<br>scores one")]
    steps = []
    for k in range(len(runs)):
        steps.append(f'{CSS}<div class="runs">' + "".join(
            run_box(n, m, c, w, i > k) for i, (n, m, c, w) in enumerate(runs)) + "</div>"
            f'<div class="small" style="margin-top:22px">A test whose result changes between runs is flaky and '
            f'is never required.</div>')
    S.append(dict(id="s04", title="Kept only if execution says so", steps=steps,
                  text="A task survives only if its new tests fail twice without the change, pass three times with "
                       "it, and still pass when their source text is held back. Then the do-nothing agent must score "
                       "zero and the oracle one, in the image the task ships with."))
    code = ("test('the label names version 2', () =>\n"
            "  assert.equal(LABEL, 'calc v2'));            <span style=\"color:" + GREEN + "\">// behaviour: kept</span>\n\n"
            "test('the label is a const export', () =>\n"
            "  assert.match(readFileSync('../lib/calc.mjs', 'utf8'),\n"
            "               /export const LABEL = /));      <span style=\"color:" + RED + "\">// greps the source</span>")
    S.append(dict(id="s05", title="Tests that read the source", steps=[
        f'{CSS}<pre class="code">{code}</pre>',
        f'{CSS}<pre class="code">{code}</pre><div class="lead" style="font-size:28px;margin-top:8px">'
        f'The probe gives test code the <span class="k">old text</span> of changed files. Tests that still pass '
        f'check behaviour; the others are never required. In the dogfood run they were '
        f'{NUM["source_text_dropped"]:,} of {NUM["f2p_total"]:,} fail-to-pass tests.</div>'],
        text="Some tests read the source and match its text. They pass for one spelling of the fix. "
             "A probe hands test code the old text of changed files. In our run, over half of the fail-to-pass "
             "tests failed it, and none of those is ever required."))
    lines = [("# feat: multiplyExact", "pull request title"), ("", ""),
             ("These tests must pass:", ""), ("- multiplies two integers", "test runner's names"),
             ("- refuses a fraction", ""), ("", ""),
             ("## Names the tests use that do not exist yet", "computed contract"), ("- `multiplyExact`", ""),
             ("", ""), ("## Exact text the tests look for", "computed contract"),
             ("- `multiplyExact needs integers`", "")]
    grid = "".join(
        f'<div style="white-space:pre">{e(a) or "&nbsp;"}</div>'
        f'<div style="color:{ORANGE if b else MUTED};text-align:right">{e(b)}</div>' for a, b in lines)
    instr = (f'<div class="code" style="display:grid;grid-template-columns:1fr auto;column-gap:40px;'
             f'font-family:{MONO};font-size:23px;line-height:1.55;background:{INK2};border-radius:16px;'
             f'padding:28px 32px;border:1px solid #2E3A4A;color:#E6EAF0">{grid}</div>')
    S.append(dict(id="s06", title="No sentence written by a model", steps=[f'{CSS}{instr}'],
        text="No sentence in a task is written by a model. The instruction is the pull request title, the issue "
             "it closed, the names of the failing tests, and the new names and strings those tests expect."))
    S.append(dict(id="s07", title="Dogfood: foxl-ai/pilot", steps=[
        f'{CSS}{funnel(0)}', f'{CSS}{funnel(1)}',
        f'{CSS}{funnel(2)}<div class="small" style="margin-top:20px">{NUM["profiles"]} test runners '
        f'&nbsp;&middot;&nbsp; {NUM["f2p_tasks_median"]} fail-to-pass tests per task (median) &nbsp;&middot;&nbsp; '
        f'every refusal recorded with its reason</div>'],
        text=f"We pointed it at {NUM['units']:,} merges in our own monorepo. {NUM['candidates']} pull requests "
             f"changed code and tests together. {NUM['accepted']} became tasks that passed every check."))
    term = (f'<span class="muted"># Harbor 0.27.0, {NUM["harbor_trials"]} tasks</span>\n'
            f'<span class="k">$</span> harbor run -p tasks -a oracle\n'
            f'  {NUM["harbor_trials"]} trials   mean reward <span style="color:{GREEN}">{NUM["oracle_mean"]}</span>\n\n'
            f'<span class="k">$</span> harbor run -p tasks -a nop\n'
            f'  {NUM["harbor_trials"]} trials   mean reward <span style="color:{RED}">{NUM["nop_mean"]}</span>')
    S.append(dict(id="s08", title="Standard Harbor tasks", steps=[f'{CSS}<div class="term">{term}</div>'],
                  text=f"The result is a standard Harbor dataset. Harbor's own oracle agent scored "
                       f"{NUM['oracle_words']} on every task, and its do-nothing agent zero."))
    cmds = ('<span class="k">$</span> taskmine scan     --recipe taskmine.toml --repo . --out out\n'
            '<span class="k">$</span> taskmine build    --recipe taskmine.toml --repo . --out out\n'
            '<span class="k">$</span> taskmine validate --recipe taskmine.toml --out out\n'
            '<span class="k">$</span> taskmine finalize --recipe taskmine.toml --out out\n\n'
            '<span class="k">$</span> harbor run -p out/tasks -a claude-code -m &lt;model&gt;')
    S.append(dict(id="s09", title="Run it on your repository", steps=[f'{CSS}<div class="term">{cmds}</div>'],
                  text="Write one recipe for your repository, run four commands, and compare your agents on the "
                       "work your team already shipped."))
    return S


async def render_frames(only: set[str] | None = None) -> None:
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for sc in scenes():
            if only and sc["id"] not in only:
                continue
            for j, body in enumerate(sc["steps"]):
                doc = server._build_slide_html(body, "html", sc["title"], "python", "aws2026-dark", W, H)
                await page.set_content(doc, wait_until="load")
                await page.evaluate("document.fonts.ready")
                await page.screenshot(path=str(OUT / f"{sc['id']}_{j}.png"), type="png")
        await browser.close()


async def tts(only: set[str] | None = None) -> None:
    (OUT / "audio").mkdir(parents=True, exist_ok=True)
    dpath = OUT / "audio" / "durations.json"
    meta = json.loads(dpath.read_text()) if dpath.exists() else {}
    for sc in scenes():
        if only and sc["id"] not in only:
            continue
        path = OUT / "audio" / f"{sc['id']}.mp3"
        r = await server.generate_tts(sc["text"], voice_id="Matthew", engine="generative", output_path=str(path))
        if "error" in r:
            raise SystemExit(f"{sc['id']}: {r['error']}")
        meta[sc["id"]] = r["duration_s"]
        print(sc["id"], r["duration_s"])
    (OUT / "audio" / "durations.json").write_text(json.dumps(meta, indent=1))


def sh(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(p.stderr[-2000:])


def video() -> None:
    dur = json.loads((OUT / "audio" / "durations.json").read_text())
    clips = []
    for sc in scenes():
        d = dur[sc["id"]] + 0.6  # breathing room after the line
        n = len(sc["steps"])
        # Build steps land at even points through the narration; the last step holds longest.
        seg = [d * 0.75 / n] * (n - 1) + [d - d * 0.75 / n * (n - 1)] if n > 1 else [d]
        clip = OUT / f"{sc['id']}.mp4"
        inputs, filt = [], []
        for j, s in enumerate(seg):
            extra = STEP_X if j < n - 1 else 0
            inputs += ["-loop", "1", "-t", f"{s + extra:.3f}", "-i", str(OUT / f"{sc['id']}_{j}.png")]
            filt.append(f"[{j}:v]scale={W}:{H}:flags=lanczos,fps={FPS},format=yuv420p,setsar=1[i{j}]")
        last, t = "i0", 0.0
        for j in range(1, n):
            t += seg[j - 1]
            filt.append(f"[{last}][i{j}]xfade=transition=fade:duration={STEP_X}:offset={t:.3f}[x{j}]")
            last = f"x{j}"
        sh([FF, "-y", *inputs, "-filter_complex", ";".join(filt), "-map", f"[{last}]", "-t", f"{d:.3f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS), str(clip)])
        clips.append((clip, d, OUT / "audio" / f"{sc['id']}.mp3"))

    inputs, vf, af = [], [], []
    for i, (clip, d, _) in enumerate(clips):
        inputs += ["-i", str(clip)]
    for i, (_, _, a) in enumerate(clips):
        inputs += ["-i", str(a)]
    last, t, starts = "0:v", 0.0, [0.0]
    for i in range(1, len(clips)):
        t += clips[i - 1][1] - X
        starts.append(t)
        vf.append(f"[{last}][{i}:v]xfade=transition=fade:duration={X}:offset={t:.3f}[v{i}]")
        last = f"v{i}"
    total = starts[-1] + clips[-1][1]
    vf.append(f"[{last}]fade=t=in:st=0:d=0.6,fade=t=out:st={total - 0.8:.3f}:d=0.8[vout]")
    k = len(clips)
    for i in range(k):
        ms = int((starts[i] + 0.35) * 1000)
        af.append(f"[{k + i}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
    af.append("".join(f"[a{i}]" for i in range(k)) + f"amix=inputs={k}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    final = HERE / "taskmine.mp4"
    sh([FF, "-y", *inputs, "-filter_complex", ";".join(vf + af), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(final)])
    sh([FF, "-y", "-ss", "1.2", "-i", str(final), "-frames:v", "1", str(HERE / "taskmine-poster.png")])
    # A short silent preview for renderers that cannot play video: the proof scene and the funnel.
    # Starts after the cross-fade into the proof scene; no dithering (it draws a grid on the flat dark background).
    s4 = starts[3] + X + 0.1
    sh([FF, "-y", "-ss", f"{s4:.2f}", "-t", "10", "-i", str(final), "-vf",
        "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];"
        "[b][p]paletteuse=dither=none",
        str(HERE / "taskmine-preview.gif")])
    print(json.dumps({"mp4": str(final), "seconds": round(total, 2), "bytes": final.stat().st_size}))


async def qa() -> None:
    r = await server.video_qa(str(HERE / "taskmine.mp4"))
    (HERE / "qa.json").write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1)[:3000])


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "frames":
        asyncio.run(render_frames(set(sys.argv[2:]) or None))
    elif cmd == "tts":
        asyncio.run(tts(set(sys.argv[2:]) or None))
    elif cmd == "video":
        video()
    elif cmd == "qa":
        asyncio.run(qa())
