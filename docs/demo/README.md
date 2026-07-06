# Ossia Web UI — Demo

Visual artifacts captured from a live `make dev-all-web` run against the
Ossia backend with Postgres + `ENABLE_HUMAN_REVIEW=true`. All screenshots
are at 1280×577 viewport.

## Videos

- **`ossia-demo-xfade.mp4`** (21s, 571KB) — recommended for sharing. 8
  scenes with smooth `xfade` transitions.
- **`ossia-demo.mp4`** (28s, 218KB) — same 8 scenes with hard cuts.

## Screenshots

| # | File | Shows |
|---|------|-------|
| 1 | `01-empty-state.png` | "Where should we begin?" centered empty state with suggestion chips |
| 2 | `02-sidebar-open.png` | Session sidebar with thread list (titles + relative timestamps) |
| 3 | `03-streaming.png` | User message + streaming assistant placeholder with full action bar |
| 4 | `04-response-complete.png` | Full markdown response (headings, bullets, inline code) |
| 5 | `05-hitl-interrupt.png` | 🛡️ Human review required card with action request preview + Approve / Reject |
| 6 | `06-hitl-approved.png` | HITL resumed run, final agent response |
| 7 | `07-code-response.png` | Python code block with Shiki syntax highlighting |
| 8 | `08-code-full.png` | Full code block + assistant action bar (Copy, Like, Dislike, Read aloud, Share, Regenerate, More) |

## How to regenerate

```bash
# Start the stack
docker compose up -d postgres
ENABLE_HUMAN_REVIEW=true POSTGRES_URL=postgresql://ossia:ossia@127.0.0.1:5432/ossia \
  OSSIA_API_KEY=dev .venv/bin/python -m uvicorn core.api:app --host 127.0.0.1 --port 8000 &
cd src/webui && npx vite --host 127.0.0.1 --port 5173 &

# Open with agent-browser
agent-browser open --args "--no-sandbox" --viewport 1280,800 http://127.0.0.1:5173
agent-browser screenshot  # saves to ~/.agent-browser/tmp/screenshots/

# Build the video
for f in 0*.png; do
  ffmpeg -y -i "$f" -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black" "scaled-$f"
done
ffmpeg -y -loop 1 -t 3 -i scaled-01-empty-state.png \
       -loop 1 -t 3 -i scaled-02-sidebar-open.png \
       -loop 1 -t 3 -i scaled-03-streaming.png \
       -loop 1 -t 4 -i scaled-04-response-complete.png \
       -loop 1 -t 3 -i scaled-05-hitl-interrupt.png \
       -loop 1 -t 3 -i scaled-06-hitl-approved.png \
       -loop 1 -t 3 -i scaled-07-code-response.png \
       -loop 1 -t 3 -i scaled-08-code-full.png \
       -filter_complex "
         [0][1]xfade=transition=fade:duration=0.5:offset=2.5[v01];
         [v01][2]xfade=transition=fade:duration=0.5:offset=5.0[v02];
         [v02][3]xfade=transition=fade:duration=0.5:offset=7.5[v03];
         [v03][4]xfade=transition=fade:duration=0.5:offset=10.5[v04];
         [v04][5]xfade=transition=fade:duration=0.5:offset=13.0[v05];
         [v05][6]xfade=transition=fade:duration=0.5:offset=15.5[v06];
         [v06][7]xfade=transition=fade:duration=0.5:offset=18.0[vout]
       " -map "[vout]" -c:v libx264 -pix_fmt yuv420p -r 30 ossia-demo-xfade.mp4
```
