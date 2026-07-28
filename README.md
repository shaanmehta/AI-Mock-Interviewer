# InteReviewAI

A spoken mock-interview app: pick a role from 110+ fields, answer adaptive
questions out loud, and get a scored, employer-style report at the end.

Click the link below to access the online application:
https://intereview-ai.onrender.com

## Or Run it locally

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
