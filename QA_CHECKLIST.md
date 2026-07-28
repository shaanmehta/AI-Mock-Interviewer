# Manual QA checklist

Two sections: what was **actually executed** during the rewrite, and what still
**needs a human** with real hardware, a real API key, and other browsers.

Local runs used a stub OpenAI-compatible server (via `GROQ_BASE_URL`) so the full
flow could be exercised without spending quota. That override is also a real
feature — it points the app at any OpenAI-compatible endpoint, such as a local
Ollama.

---

## ✅ Verified during the rewrite

### Automated — 60 unit tests, all passing

```bash
./.venv/bin/python -m pytest tests/ -q
```

| Area | Coverage |
| --- | --- |
| `test_scoring.py` | JSON extraction from fences/prose/nested braces; schema validation against 11 malformed shapes; legacy capitalized keys; range clamping; numeric-string coercion; score derived from rubric when absent |
| `test_vision.py` | `snapshot_and_reset()` exists, aggregates, and clears; untrusted payloads never raise; percentages computed over face-present frames; aggregator instances independent; `_global_vision`/`analyze_face` confirmed gone |
| `test_llm.py` | JSON mode toggling; 429 retry then success; typed `RateLimitError`/`AuthError`; 5xx retried, 401 not; fallback-model downgrade; provider error bodies never reach the user message; fresh client per call |

Lint clean: `ruff check app.py interview/ tests/ --select F,E9`.

### End-to-end interview, walked in a browser

- Full flow **setup → recording setup → 5 questions → scored report**.
- Setup form: all fields, 110+ job fields, question-count slider.
- Question stage: question card, per-question answer buffers correctly isolated
  (a new question starts with an empty box), submit advances, last question
  correctly reads **"Finish and get my report"**.
- Results dashboard: score hero (78/100 + verdict), rubric radar and ranked bar
  charts, strengths/fixes columns, 5 expandable per-question cards, delivery
  notes, both download buttons, and "Start a new interview".

### Structured scoring output

Validation is a **total function** — it always returns the canonical schema.
Verified against 11 malformed inputs including `None`, bare strings, wrong types,
out-of-range numbers, and legacy capitalized keys. This is what makes "valid
structured JSON every time" true by construction rather than by luck.

### TTS playback

- Component rendered, browser voice list enumerated successfully (`Samantha
  (en-US)` among others), Play/Stop/voice picker/speed all present.
- Autoplay fired on question load — observed via the Play button entering its
  disabled/speaking state.

### Camera availability honesty (the fix for bug #2)

- With camera access blocked, the app reported **"Camera permission denied"** in
  both the in-component overlay and the server-rendered status chip, alongside a
  reassuring "answers are scored on content, not appearance" message.
- This also proves the hand-written component protocol round-trips: the status
  reached `st.session_state` and was rendered server-side.

### Timer

- Counts down live (`0:57 left on this answer`) via `st.fragment(run_every=1)`.
- Expiry auto-submits and advances to the next question; answers left empty at
  expiry are recorded as "(No answer was given before time ran out.)" rather than
  hanging.

### Error handling and graceful degradation

- Pointed at a dead endpoint: showed **"Could not reach the AI provider. Check
  your connection and retry."** with **Try again** and **Use a standard
  question** buttons. No stack trace, no raw exception text.
- **Use a standard question** worked — the interview continued with a static
  question while the provider was completely unreachable.
- Empty submit is refused with a clear message rather than recording a blank.

### Exports

- Markdown (1.1 KB) and printable HTML (4.1 KB) both generate.
- **HTML escaping verified**: a `<script>alert(1)</script>` payload injected via
  LLM output and transcript text came out escaped in the printable report.

### Mobile (375×812)

- Found and fixed a real bug: the rubric radar collapsed at phone width because
  fixed margins plus long axis labels consumed the entire viewport. Fixed with
  short labels, auto-expanding margins, and by making the (narrow-friendly,
  more actionable) ranked bar chart the default tab.
- Also fixed inverted bar ordering — Plotly draws the first bar at the bottom, so
  "weakest first" was rendering weakest *last*.
- Re-verified: score hero stacks, both charts readable, no horizontal scroll.

### Dependency and config hygiene

- `mediapipe` now ships `py3-none-*` wheels (0.10.35) — but it is removed
  entirely anyway, along with `opencv-python`, `av`, `streamlit-webrtc` and
  `streamlit-autorefresh`. Ten dependencies down to five.
- MediaPipe CDN and model URLs confirmed live (HTTP 200): `vision_bundle.mjs`,
  the WASM binary (11 MB), and `face_landmarker.task` (3.7 MB).
- Fixed a config conflict Streamlit warned about: `enableCORS=false` is
  incompatible with `enableXsrfProtection=true` and was being silently overridden.
- Migrated off `st.components.v1.html`, which is deprecated past 2026-06-01, to
  `st.iframe`. Server starts with **zero warnings**.
- `.env` confirmed git-ignored (`git check-ignore`).

---

## ⚠️ Still needs a human

These could not be exercised in a headless environment.

### 1. Real microphone, two browsers

- [ ] **Chrome/Edge**: "Live transcription" — click **Start talking**, speak,
      click **Stop**, confirm the transcript lands in the box.
- [ ] **Safari**: confirm it defaults to "Record, then transcribe", and that the
      Groq Whisper fallback returns a real transcript.
- [ ] **Firefox**: same as Safari.
- [ ] Confirm the mode warning appears when live transcription is picked on a
      browser that doesn't support it.

> Only the *plumbing* around STT was verified. No real audio was captured, so the
> transcription quality of either path is unverified.

### 2. Real webcam

- [ ] Grant camera access and confirm the status chip turns green
      ("Face analysis active") and the sample counter climbs.
- [ ] Confirm the bounding box tracks your face.
- [ ] Sanity-check the metrics: looking straight ahead should give high
      `centered_pct` and `eye_contact_pct`; turning away should drop them.
- [ ] Confirm the ~15 MB first load is acceptable on your connection, and that a
      second visit is fast (browser-cached).
- [ ] Check CPU on a mid-range phone — detection is capped at ~6 fps, but verify
      it doesn't cook the device.

> The geometric thresholds (symmetry > 0.62, nose-ratio band 0.32–0.72) are
> reasoned from landmark geometry but **were never calibrated against a real
> face**. Expect to tune them.

### 3. Real Groq key

- [ ] Add a real `GROQ_API_KEY` and run a full interview.
- [ ] Confirm question quality and adaptivity — that follow-ups actually build on
      your previous answers.
- [ ] Confirm `llama-3.3-70b-versatile` reliably honours JSON mode. Validation
      will catch failures, but if `_meta.was_repaired` is often true, the prompt
      needs work.
- [ ] Test bring-your-own-key in the sidebar with a second key.

### 4. Rate-limit behaviour under real load

- [ ] The 429 path is unit-tested but never hit a live Groq 429. Worth confirming
      the "interviewer is catching its breath" message appears rather than
      something uglier.

### 5. Deployment

- [ ] Deploy to Render and confirm the build succeeds within 512 MB.
- [ ] Confirm the WebSocket connection is stable (Streamlit will show
      "Connecting…" forever if not).
- [ ] Add the custom domain, confirm DNS propagates and SSL is issued.
- [ ] **Measure the cold start after 15 minutes idle** and decide whether it is
      acceptable or whether the Oracle path is worth the setup.
- [ ] Confirm camera/mic prompts work over HTTPS on the real domain — a secure
      context is mandatory off localhost.

### 6. Mobile hardware

- [ ] Real iOS Safari and Android Chrome: camera and mic permission prompts,
      and whether the in-page camera preview behaves on a real device.
