# Regenerating the overview video

`make_video.py` uses the internal **video-generator** plugin (its `video-render` MCP server's
Python module, `server.py`) for the AWS 2026 slide theme and logo, Amazon Polly TTS and Nova 2 Lite
QA. The plugin is not part of this repository.

```sh
# next to this file: the plugin's mcps/video-render directory, installed from its own lockfile
cp -R <video-generator>/mcps/video-render ./video-render
(cd video-render && uv sync --frozen --python 3.12 && .venv/bin/playwright install chromium)

python3 gen_numbers.py <dataset dir> > numbers.json   # every on-screen number comes from the dataset
video-render/.venv/bin/python make_video.py frames    # slides at 2x, for a look
video-render/.venv/bin/python make_video.py tts       # Polly, voice Matthew (generative); needs AWS credentials
video-render/.venv/bin/python make_video.py video     # taskmine.mp4, poster, GIF preview
video-render/.venv/bin/python make_video.py qa        # Nova 2 Lite scores -> qa.json
```

Requirements: ffmpeg, the Amazon Ember fonts (the theme falls back to system sans without them),
and AWS credentials allowed `polly:SynthesizeSpeech` and Bedrock `us.amazon.nova-lite-v1:0` in
us-west-2.
