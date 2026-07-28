# Architecture decisions

Everything below was re-verified on **2026-07-27**. Free tiers move fast — treat
the numbers as a snapshot and re-check the linked pages before relying on them.

## The governing constraint

**Zero marginal cost to the owner at any traffic level.** Two mechanisms satisfy
this, and every capability uses one of them:

1. **Hard-limited free tiers with no card on file.** Exceeding the limit returns
   HTTP 429. There is no overage billing because there is no payment method.
2. **Client-side execution.** The work happens in the visitor's browser. No
   server call exists, so there is nothing to bill.

Anything metered against a personal card was removed. The OpenAI dependency is
gone entirely.

---

## 1. LLM — question generation + scoring

**Chosen: Groq free tier**, called over its OpenAI-compatible REST API.
**Secondary: Google Gemini** free tier, behind the identical interface.

### Why

| Option | Verdict |
| --- | --- |
| **Groq** | No credit card. Generous open-model limits. Very low latency, which matters because a question is generated between every answer. OpenAI-compatible, so no vendor SDK. |
| **Gemini** | Also card-free; kept as a second provider for redundancy if Groq's shared quota becomes a bottleneck. |
| OpenAI (status quo) | Metered against a personal card. Disqualified. |

### Free-tier limits (verified 2026-07-27)

| Model | Used for | RPM | RPD | TPM | TPD |
| --- | --- | --- | --- | --- | --- |
| `llama-3.1-8b-instant` | questions, fallback | 30 | 14,400 | 6,000 | 500,000 |
| `llama-3.3-70b-versatile` | scoring | 30 | 1,000 | 12,000 | 100,000 |
| `whisper-large-v3-turbo` | fallback STT | 20 | 2,000 | 7,200 audio-sec/hr | 28,800/day |

Source: <https://console.groq.com/docs/rate-limits>

The model split is deliberate. Question generation is the high-frequency call
(N per interview) so it uses the 8B model with a ~14x larger daily budget;
scoring happens once per interview and gets the stronger 70B model.

### The shared-quota problem

Free-tier limits are **per organization, not per visitor**. Every stranger on the
deployment draws from one bucket, so throttling is a normal operating state, not
an exception. Handling, in `interview/llm.py`:

- 429 → retry with exponential backoff + jitter, honouring `Retry-After`.
- Still 429 → automatically retry against `FALLBACK_MODEL` (the 8B model, with
  14x the daily headroom).
- Still failing → a calm user-facing message and a **Try again** button. The
  interview is never left in a broken state; there is also a **Use a standard
  question** escape hatch backed by a static question bank.
- **Bring your own key**: a visitor can paste their own free Groq/Gemini key in
  the sidebar, which removes them from the shared bucket entirely. Kept in
  session memory only — never logged, never persisted.

### Structured output

The old scorer asked for JSON in prose, and the prompt's own example was invalid
JSON (unquoted keys, a stray `: :`, an unterminated string). Parsing failed
often and silently degraded to an empty rubric.

Now, in layers:

1. Provider-enforced JSON mode (`response_format: {"type": "json_object"}`).
2. Defensive extraction — code fences, prose wrappers, brace matching that
   ignores braces inside strings.
3. One repair round-trip that feeds malformed output back for correction.
4. `validate_result()` — a **total** function that coerces types, clamps ranges,
   fills defaults, and accepts legacy capitalized keys. It always returns the
   canonical schema, so the dashboard renders without defensive checks.

The canonical schema is **snake_case everywhere** — prompt, parser, and UI.

---

## 2. Text-to-speech — the interviewer's voice

**Chosen: browser `SpeechSynthesis`** via a self-contained HTML component.

Zero server calls, so it scales to unlimited concurrent users and cannot be
billed. Voice selection and speed live *inside* the component (persisted to
`localStorage`), so changing them triggers no Streamlit rerun.

**Known limits.** Voice quality and availability vary by OS and browser. Most
browsers require a user gesture before speech; autoplay is attempted as an
enhancement and a **Play question** button is always present. Where speech
synthesis is missing entirely, the component says so plainly. The question text
is always on screen — **audio is never load-bearing.**

The question text is LLM output and therefore untrusted. It is injected only
inside a `<script type="application/json">` block, JSON-escaped with `</`
additionally neutralised, so it can never execute as markup or JS.

---

## 3. Speech-to-text — the candidate's answer

**Primary: browser `SpeechRecognition`** (unchanged — already free and
client-side, via `streamlit-mic-recorder`).
**Fallback: Groq `whisper-large-v3-turbo`.**

Live recognition is solid in Chrome and Edge and unreliable-to-absent in Safari
and Firefox. The app defaults to the fallback mode when the User-Agent looks
like Safari or Firefox, tells the user which mode they're in, and lets them
switch manually. Typing an answer is always available and scored identically.

The old `engine.transcribe_audio_bytes` (OpenAI Whisper) was dead code — nothing
called it. It is replaced by `interview/transcribe.py` on the free Groq endpoint.

---

## 4. Face analytics — **Option A, fully client-side**

**Chosen: MediaPipe `@mediapipe/tasks-vision` FaceLandmarker running as WASM in
the visitor's browser.**

### Why not server-side (Option B)

Unlike the LLM calls, server-side OpenCV + MediaPipe is not rate-limited by a
vendor — it is raw CPU and RAM on the host. Running per-frame detection for
every concurrent visitor on a 512 MB free instance falls over after roughly one
or two simultaneous camera sessions. There is no configuration that makes that
scale for free.

Moving detection into the browser removes the bottleneck entirely: **the server
does zero per-frame work regardless of how many people are using the site.** It
also let the dependency list shrink from 10 packages to 5 — `opencv-python`,
`mediapipe`, `av`, `streamlit-webrtc` and `streamlit-autorefresh` are all gone,
which is what makes the app fit in 512 MB with fast cold starts.

### How it works

`interview/ui/frontend/face_monitor/index.html` implements the Streamlit
component postMessage protocol **by hand** — so there is no npm toolchain and no
build step in this repo.

- `getUserMedia` at 320×240, detection throttled to ~6 fps to bound CPU on the
  visitor's device (this matters on phones).
- Per-sample metrics derived from landmark geometry: face presence, bounding-box
  centre, centeredness, and a convention-free frontal-facing proxy built from
  nose/eye-corner symmetry and nose-to-brow-chin ratio.
- Only small **delta batches** of aggregate samples are posted back, every
  ~2.5 s. Raw video never leaves the device and nothing is recorded.
- The panel lives inside `st.fragment`, so its posts rerun **only that fragment**
  — the mic recorder and the answer box are never disturbed.
- `interview/vision.py` validates every incoming sample defensively; malformed
  client data can never corrupt the aggregate.

**Costs to be aware of:** first load pulls ~11 MB of WASM plus a 3.7 MB model
from jsDelivr and Google's model CDN (browser-cached afterwards). Requires
WebAssembly and a secure context (HTTPS or localhost).

Microphone-only remains a **fully first-class path**. Camera metrics are a
lightly-weighted signal and the scorer prompt is explicitly instructed not to
let them move the score much.

---

## 5. Hosting — Render free tier

| Option | Custom domain on free? | Verdict |
| --- | --- | --- |
| **Render free** | **Yes**, with free SSL | **Chosen.** No card required; hard-suspends rather than bills. |
| Streamlit Community Cloud | No — subdomain only | Rejected: the requirement is a real domain. |
| Hugging Face Spaces | No — PRO/Team only | Rejected: custom domains need a paid plan. |
| Fly.io | n/a | Rejected: free allowances discontinued; ~$5/mo minimum, card required. |
| Railway | n/a | Rejected: permanent free tier removed; $5/mo Hobby. |
| Oracle Always Free + Cloudflare Tunnel | Yes | **Documented as the no-cold-start alternative.** |

### Render free tier, honestly

- **512 MB RAM, 0.1 CPU.** Fine for this app now that vision is client-side.
- **Sleeps after 15 minutes of inactivity**, and the next visitor waits **~1
  minute** for a cold start. This is the single biggest downside for a "polished
  public product", and it is unavoidable on this tier. Mitigations: a paid
  instance ($7/mo) removes it, or use the Oracle path below. An external uptime
  pinger technically works but burns the 750-hour monthly budget and is against
  the spirit of the free tier.
- **750 instance-hours per workspace per month.** One always-awake service would
  exceed this; a sleeping one will not.
- Streamlit needs persistent WebSockets — Render supports them.

### Alternative: Oracle Cloud Always Free + Cloudflare Tunnel

No sleep, no cold start, far more RAM. As of 2026-06-15 the Ampere A1 always-free
allowance is 2 OCPU / 12 GB. Trade-offs: Oracle requires a card for identity
verification (not charged on Always Free), the signup is fiddly, and you own the
VM patching. Cloudflare Tunnel + Cloudflare DNS are free and avoid opening any
inbound ports. See the README for the deploy steps.

---

## Multi-user safety

- **No module-level mutable state anywhere in `interview/`.** The old
  `vision._global_vision` singleton was shared by every concurrent visitor in the
  process; it is deleted, and `tests/test_vision.py` asserts it stays deleted.
- Every per-user value lives in `st.session_state`, created once in
  `interview/session.py`.
- Provider clients are constructed per call and hold only an API key, so no
  visitor's key can leak into another session. A test asserts each call returns a
  fresh instance.
- Cross-page settings use plain session keys, **not** widget keys — Streamlit
  garbage-collects widget state once the widget stops rendering, which silently
  loses the value on navigation.
- `showErrorDetails = false` in production, and every external call is wrapped so
  strangers see a friendly message, never a stack trace or raw provider text.

## Abuse protection

Soft, per-session, and all configurable via env vars: caps on questions per
interview, interviews per session, and total question generations; a minimum
interval between provider calls; and a cap on fallback-STT upload size. Visitors
using their own key bypass the throttle, since they are spending their own quota.
