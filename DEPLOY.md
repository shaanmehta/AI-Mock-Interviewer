# Deploying InteReviewAI — exact steps

Target: a public, working URL at **`https://intereview-ai.onrender.com`**, at
**$0/month**, with no credit card anywhere in the process.

Verified 2026-07-28. Follow the steps in order; each one says what you should
see before moving on.

---

## Why Render

| Host | Free public URL | Card required | Verdict |
| --- | --- | --- | --- |
| **Render** | `*.onrender.com` + free SSL | **No** | **Chosen.** Hard-suspends instead of billing. |
| Streamlit Community Cloud | `*.streamlit.app` | No | Viable, but ties the app to Streamlit's platform and has stricter resource limits. |
| Hugging Face Spaces | `*.hf.space` | No | Streamlit needs the Docker template; more moving parts. |
| Fly.io | — | Yes | Free allowances discontinued (~$5/mo minimum). |
| Railway | — | Yes | Permanent free tier removed ($5/mo Hobby). |

**The one trade-off, stated plainly:** a free Render service **sleeps after 15
minutes of inactivity**, and the next visitor waits **about a minute** for it to
wake. There is no free way around this. If that is unacceptable, Render's
Starter plan is $7/month and removes it.

---

## Step 1 — Get your Groq API key (2 min)

1. Go to <https://console.groq.com/keys>.
2. Sign in with GitHub or Google.
3. Click **Create API Key**, name it `intereview`, and copy the value.

It starts with `gsk_`. **No credit card is requested at any point.** If you
exceed the free limits the API returns HTTP 429 — it cannot bill you.

> Keep this tab open; you'll paste the key in step 4.

---

## Step 2 — Push the code to GitHub (2 min)

From the project folder:

```bash
git checkout main && git merge --ff-only rebuild/free-tier-rearchitecture
```

```bash
git push origin main
```

**Check:** open <https://github.com/shaanmehta/InteReviewAI> and confirm you can
see `app.py`, `render.yaml`, `requirements.txt` and `.python-version`.

> `.env` is git-ignored, so your local key is not pushed. That is intentional.

---

## Step 3 — Create the Render service (3 min)

1. Go to <https://dashboard.render.com> and sign up **with GitHub**.
2. Click **New +** → **Blueprint**.
3. Pick the `InteReviewAI` repository. If it isn't listed, click **Configure
   account** and grant Render access to it.
4. Render reads `render.yaml` and shows a service named **intereview-ai**.
5. It will prompt for the two secret values from `render.yaml`:
   - **GROQ_API_KEY** → paste the `gsk_...` key from step 1.
   - **ANALYTICS_PASSWORD** → invent a strong password. This is what protects
     your private analytics page. Save it somewhere.
6. Click **Apply**.

**Check:** the build log ends with `==> Build successful` then
`You can now view your Streamlit app in your browser`. First build takes 2–4
minutes.

---

## Step 4 — Confirm the site is live (1 min)

Your URL is shown at the top of the service page, in the form
`https://intereview-ai.onrender.com` (Render appends a suffix if the name is
taken — use whatever it shows).

Open it in a **private/incognito window**, which is how a stranger sees it.

**Check, in order:**
- The page loads and says **InteReviewAI**.
- The progress bar shows **SETUP** and is empty.
- The setup form is visible — **not** the "no AI provider is configured" error.
  If you see that error, your `GROQ_API_KEY` didn't save; go to step 7.
- Run one interview end to end. Questions should match the job field you chose.

---

## Step 5 — Check your private analytics (1 min)

Visit **`https://intereview-ai.onrender.com/?admin=1`**.

Enter the `ANALYTICS_PASSWORD` from step 3.

You'll see:
- **Opened the site** — unique visitors
- **Started an interview** — clicked *Start interview*, with conversion %
- **Finished an interview** — reached the report, with conversion %
- Questions answered, average score, errors surfaced
- Breakdowns by job field, recording mode and speech-to-text mode
- Score distribution, a recent-events table, and a JSON export

This page is **not linked from anywhere** in the UI and refuses to open without
the password. Visitors cannot reach it.

> The dashboard will warn **"Storage: SQLite (resets on restart)"**. That is
> expected until you do step 6.

---

## Step 6 — Make analytics survive restarts (5 min, recommended)

Render's free tier has **no persistent disk**: the filesystem is wiped every
time the service sleeps, wakes, or redeploys. Your counts would reset roughly
daily. Free Postgres fixes it.

1. Go to <https://supabase.com> → **Start your project** → sign in with GitHub.
   **No credit card required.**
2. **New project.** Name it `intereview`, pick any region, set a database
   password (you won't need it again). Wait ~2 min for provisioning.
3. In the left sidebar open **SQL Editor** → **New query**, paste this, and click
   **Run**:

```sql
create table if not exists events (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  session_id text not null,
  event text not null,
  props jsonb not null default '{}'::jsonb
);
create index if not exists events_event_idx on events (event);
create index if not exists events_session_idx on events (session_id);
alter table events enable row level security;
```

   Enabling row-level security with **no policies** means the public `anon` key
   cannot read or write this table at all — only the `service_role` key can.
   That is exactly what we want: analytics are yours alone.

4. Go to **Project Settings** → **API keys**. Copy:
   - **Project URL** (looks like `https://abcdefgh.supabase.co`)
   - the **`service_role`** key (click *Reveal*)

   > Use `service_role`, **not** `anon`. The `anon` key is blocked by the RLS
   > policy above and analytics writes would silently do nothing.

5. Back in Render: your service → **Environment** → **Add Environment Variable**,
   twice:
   - `SUPABASE_URL` = the Project URL
   - `SUPABASE_KEY` = the `service_role` key
6. Click **Save, rebuild, and deploy**.

**Check:** reload `/?admin=1`. The chip should now read
**"Storage: Supabase (durable)"** in green.

> Free Supabase projects **pause after 7 days with no database activity**. Any
> visitor to your site writes an event, so a site with traffic never pauses. If
> yours goes quiet for a week, unpause it from the Supabase dashboard — no data
> is lost, and analytics writes fail silently in the meantime rather than
> breaking the site.

---

## Step 7 — If something goes wrong

**"no AI provider is configured" on the live site**
Render → your service → **Environment**. Confirm `GROQ_API_KEY` exists and
starts with `gsk_`. Placeholder values like `gsk_replace_me` are deliberately
treated as missing. Save and redeploy.

**Build fails on `pip install`**
Check the log for a Python version. It must be 3.12.x. If it shows 3.14,
`.python-version` didn't get committed — confirm the file exists at the repo
root with the contents `3.12`, then push again. Do **not** add a
`PYTHON_VERSION` environment variable; it overrides `.python-version` and needs
an exact patch release.

**Page loads but spins on "Connecting…" forever**
This is a WebSocket problem. On Render it should not happen; if you later add a
proxy such as Cloudflare, set the DNS record to **DNS only** (grey cloud) or
enable WebSockets on the proxy.

**"The interviewer is catching its breath"**
That is the Groq free-tier rate limit, shared by everyone using your site. It
resolves on its own. Visitors can bypass it by adding their own key in
**Settings »**.

**Analytics page says "Analytics are disabled"**
`ANALYTICS_PASSWORD` isn't set. Add it in Render → Environment.

**Service is slow on first visit**
That's the 15-minute sleep. Expected on the free plan.

---

## What it costs

| Item | Cost |
| --- | --- |
| Render free web service | $0 |
| `*.onrender.com` URL + SSL | $0 |
| Groq API (free tier) | $0 — no card, 429s instead of charges |
| Supabase free Postgres | $0 — no card |
| **Total** | **$0/month** |

No credit card is entered anywhere. Every service hard-limits instead of
billing, so there is no path to an unexpected charge no matter how much traffic
the site gets.

---

## Free-tier limits worth knowing

| Limit | Value |
| --- | --- |
| Render RAM / CPU | 512 MB / 0.1 CPU |
| Render sleep | after 15 min idle, ~1 min cold start |
| Render hours | 750 instance-hours per workspace per month |
| Groq `llama-3.1-8b-instant` (questions) | 30 req/min, 14,400 req/day |
| Groq `llama-3.3-70b-versatile` (scoring) | 30 req/min, 1,000 req/day |
| Groq `whisper-large-v3-turbo` (fallback STT) | 20 req/min, 2,000 req/day |
| Supabase | 500 MB database, pauses after 7 days idle |

At 5 questions per interview, the daily budget supports roughly **1,000 scored
interviews per day** before the scoring model throttles — and throttling shows a
polite retry message, never an error page.
