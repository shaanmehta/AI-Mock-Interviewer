# InteReviewAI

A spoken mock-interview app: pick a role from 110+ fields, answer adaptive
questions out loud, and get a scored, employer-style report at the end.

**It costs $0 to run, at any traffic level.** Every paid dependency was replaced
with either a card-free rate-limited tier or something that runs entirely in the
visitor's browser.

---

## What changed in this rewrite, and why

The previous version ran locally against the owner's personal OpenAI key, which
made publishing it to strangers a billing risk. It also had several features that
had never actually worked in production.

### Cost — OpenAI removed entirely

| Capability | Before | Now |
| --- | --- | --- |
| Questions + scoring | OpenAI (metered, personal card) | **Groq free tier** (no card, hard 429) |
| Interviewer's voice | OpenAI TTS (metered) | **Browser SpeechSynthesis** (no server call) |
| Candidate's speech | Browser API, plus dead OpenAI code | Browser API + **Groq Whisper** fallback |
| Face analysis | Server-side OpenCV/MediaPipe | **MediaPipe WASM in the browser** |

### Bugs fixed

1. **`vision.snapshot_and_reset()` didn't exist.** `app.py` called it on every
   answer submission inside a bare `except`, so face analytics silently failed
   for *every* user and always stored `{"error": "snapshot failed"}`. The
   feature had never worked. Now implemented, and unit-tested.
2. **`vision_available` was hardcoded `True`.** The UI confidently offered
   camera analysis even when the libraries were missing, then produced fake
   neutral metrics. Availability is now a real per-browser runtime fact
   (`VisionStatus`) and is shown honestly, including permission-denied and
   unsupported-browser states.
3. **The scorer's "STRICT JSON" template was itself invalid JSON** — unquoted
   keys, a stray `Depth Tradeoffs: : number`, and an unterminated
   `"Listening Followups`. `json.loads` failed often, silently degrading to an
   empty rubric. Fixed, plus provider-enforced JSON mode and schema validation.
4. **Key-name mismatch.** The prompt asked for `Overall Score`/`Rubric` while the
   code expected `overall_score`/`rubric`. One canonical snake_case schema is now
   used everywhere, with legacy capitalized keys still tolerated on parse.
5. **`transcribe_audio_bytes` was OpenAI-billed dead code** — nothing called it.
   Replaced by an explicit free Whisper fallback in `interview/transcribe.py`.
6. **Module-level `_global_vision` singleton** — shared across concurrent
   visitors in one process. Deleted; a test asserts it stays deleted.
7. **Overlapping media dependencies.** `streamlit-webrtc` and
   `streamlit-autorefresh` were dropped (the latter was listed but never even
   imported — which is why the countdown never ticked).
8. **No secrets story.** Added `.env.example`, `.gitignore`, and a documented
   `st.secrets` → env → `.env` resolution order.
9. **Results were a raw `st.json()` dump.** Replaced with a real dashboard.

Two more found while working, not in the original list:

10. **The camera never rendered at all.** The media page set `media_mode` to
    `"mic+cam"` while the interview page checked for `"mic+face"` — so the branch
    was dead. Combined with bugs 1 and 2, face analytics was broken three ways.
11. **The countdown never counted down.** `streamlit-autorefresh` was in
    `requirements.txt` but never imported, so the timer only moved when something
    else happened to trigger a rerun. Now a `st.fragment(run_every=1)`.

### UI

Rebuilt around a single design system (`.streamlit/config.toml` theme +
`interview/ui/theme.py`) instead of ad hoc inline HTML scattered through
`app.py`: a stage progress bar, consistent cards and pills, real loading and
error states, an honest camera-status chip, and a results dashboard with a
rubric chart, expandable per-question feedback, and a print-to-PDF export. Works
down to a 375 px phone viewport.

---

## Get your free API key

Only one key is needed.

1. Go to <https://console.groq.com/keys> and sign in (GitHub/Google works).
2. Create an API key. It starts with `gsk_`.
3. **No credit card is required.** If you exceed the free limits the API returns
   HTTP 429 — it cannot bill you.

Optional second provider for redundancy: <https://aistudio.google.com/apikey>
(`GEMINI_API_KEY`).

Visitors can also paste their *own* key into the sidebar, which takes them off
the deployment's shared rate limit. It is held in session memory only.

---

## Run it locally

```bash
git clone https://github.com/shaanmehta/InteReviewAI.git
```

```bash
cd InteReviewAI && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Put your key in `.env` (it is git-ignored):

```bash
GROQ_API_KEY=gsk_your_key_here
```

```bash
./.venv/bin/streamlit run app.py
```

Open <http://localhost:8501>. Run the tests with:

```bash
./.venv/bin/python -m pytest tests/ -q
```

> Camera and microphone need a **secure context**. `localhost` counts, so local
> development works; any other host must be HTTPS.

---

## Deploy to Render + a custom domain

Render's free tier is the only host checked that offers a custom domain with
free SSL, requires no credit card, and hard-suspends instead of billing.
**Read the cold-start caveat below before committing to it.**

### 1. Create the service

1. Push this repo to GitHub.
2. At <https://dashboard.render.com> → **New** → **Blueprint**, point it at your
   repo. Render reads `render.yaml` and configures everything.
3. When prompted for `GROQ_API_KEY`, paste your key. It is marked
   `sync: false`, so it lives in Render's secret store and never touches git.
4. Deploy. First build takes 2–3 minutes.

<details>
<summary>Manual setup instead of the Blueprint</summary>

**New → Web Service**, connect the repo, then:

- Runtime: `Python 3`
- Build command: `pip install -r requirements.txt`
- Start command:
  ```
  streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
  ```
- Environment → add `GROQ_API_KEY` as a secret.
- Health check path: `/_stcore/health`

Binding `0.0.0.0` and using `$PORT` are both required — Render will not route to
a service bound to localhost.
</details>

### 2. Point your domain at it

In Render: **Settings → Custom Domains → Add**, and enter both `yourdomain.com`
and `www.yourdomain.com`. Render shows the exact target to use, then at your DNS
provider (Cloudflare, Namecheap, Porkbun…):

| Type | Name | Value |
| --- | --- | --- |
| `A` | `@` | the IPv4 address Render displays for the apex domain |
| `CNAME` | `www` | `your-service.onrender.com` |

Apex domains cannot use `CNAME` per DNS rules, which is why the root uses an `A`
record. If your DNS provider supports `ALIAS`/`ANAME` flattening (Cloudflare
does), you may point the apex at `your-service.onrender.com` instead.

**Using Cloudflare?** Set the records to **DNS only** (grey cloud), not proxied.
Cloudflare's proxy interferes with Streamlit's WebSocket connection unless you
also enable WebSockets on the Cloudflare side.

Render issues a TLS certificate automatically once DNS resolves — usually within
a few minutes, occasionally up to an hour.

### 3. Know the trade-off

> **Free Render services sleep after 15 minutes of inactivity.** The next
> visitor waits about **a minute** for the app to wake up, staring at a blank
> page. For a public product this is the weakest part of the free setup.
>
> Options: upgrade to Render's $7/mo Starter to remove sleeping, or use the
> Oracle path below for a genuinely free always-on host. Free instance hours are
> capped at 750/workspace/month, so a service that never sleeps would exceed the
> budget anyway.

### Alternative: Oracle Always Free + Cloudflare Tunnel (no cold starts)

Genuinely free, always on, much more RAM — at the cost of a fiddlier setup and
you owning the VM.

1. Create an Oracle Cloud account and launch an **Always Free** Ampere A1
   instance (2 OCPU / 12 GB as of 2026-06-15), Ubuntu 22.04. A card is required
   for identity verification but Always Free resources are not charged.
2. On the VM:
   ```bash
   sudo apt update && sudo apt install -y docker.io git && sudo usermod -aG docker $USER
   ```
   ```bash
   git clone https://github.com/shaanmehta/InteReviewAI.git && cd InteReviewAI
   ```
   ```bash
   docker build -t intereview . && docker run -d --restart unless-stopped -p 8501:8501 -e GROQ_API_KEY=gsk_your_key intereview
   ```
3. Add your domain to Cloudflare and point your registrar at Cloudflare's
   nameservers.
4. In **Cloudflare Zero Trust → Networks → Tunnels**, create a tunnel, run the
   `cloudflared` install command it gives you on the VM, and add a public
   hostname mapping `yourdomain.com` → `http://localhost:8501`.

The tunnel dials out, so you never open an inbound port, and Cloudflare
terminates TLS for you. Nothing here costs money.

---

## Configuration

Every value has a working default; see [`.env.example`](.env.example) for the
full list. Secrets resolve in this order:

1. `st.secrets` — Streamlit Cloud / Hugging Face style
2. environment variables — Render / Docker style
3. `.env` at the repo root — local development only, git-ignored

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | — | **Required** unless every visitor brings their own key |
| `GEMINI_API_KEY` | — | Optional second provider |
| `LLM_PROVIDER` | `groq` | `groq` or `gemini` |
| `QUESTION_MODEL` | `llama-3.1-8b-instant` | High-frequency call; large daily budget |
| `SCORING_MODEL` | `llama-3.3-70b-versatile` | Once per interview; stronger model |
| `FALLBACK_MODEL` | `llama-3.1-8b-instant` | Used when the scoring model is rate-limited |
| `STT_MODEL` | `whisper-large-v3-turbo` | Fallback transcription |
| `ALLOW_USER_API_KEY` | `true` | Let visitors supply their own key |
| `GROQ_BASE_URL` | Groq's API | Point at any OpenAI-compatible endpoint (e.g. local Ollama) |
| `MAX_QUESTIONS` | `8` | Upper bound on interview length |
| `MAX_INTERVIEWS_PER_SESSION` | `12` | Soft abuse guard |
| `MAX_REGENERATIONS_PER_SESSION` | `20` | Soft abuse guard |

---

## Project layout

```
app.py                          Streamlit entry point and stage router
interview/
  config.py                     Settings + secret resolution
  llm.py                        Provider-agnostic LLM access (Groq, Gemini)
  prompts.py                    Interviewer + scorer system prompts
  questions.py                  Adaptive question generation
  scoring.py                    Schema, JSON extraction, validation, scoring
  transcribe.py                 Fallback speech-to-text
  vision.py                     Aggregation of browser-reported face samples
  session.py                    Per-user state and abuse guards
  report.py                     Markdown + printable HTML exports
  ui/
    theme.py                    Design system
    components.py               TTS + face-monitor components
    results.py                  Results dashboard
    frontend/tts/               SpeechSynthesis component
    frontend/face_monitor/      MediaPipe WASM component
tests/                          Unit tests (60)
```

## Further reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — why each provider was chosen and the exact
  limits of each free tier.
- [QA_CHECKLIST.md](QA_CHECKLIST.md) — the manual test pass, including what was
  verified and what still needs a human.

---

InteReviewAI gives practice feedback from a language model. It is not a hiring
decision, and it should not be used to evaluate real candidates.
