# InteReviewAI

A spoken mock-interview app: pick a role from 110+ fields, answer adaptive
questions out loud, and get a scored, employer-style report at the end.

**It costs $0 to run, at any traffic level.** 

Note: You need a Groq or Gemini FREE API key to run this application. See details below.

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
---

Note: InteReviewAI gives practice feedback from a language model. It is not a hiring
decision, and it should not be used to evaluate real candidates.
