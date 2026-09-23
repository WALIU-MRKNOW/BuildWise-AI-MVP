# BuildWise AI

**Plan • Learn • Analyze • Build Better**

BuildWise AI is a student-focused Telegram MVP with three workflows:

- 📚 Study & Solve — text, question photos, calculations, theory, PDFs, answer checking
- 📐 Sketch Analysis — sketches, floor plans, layouts, and architectural concepts
- 🏗️ Building Analysis — preliminary review of visible building concerns in photos

It also includes a home menu, automatic request routing, Quick Practice, study streaks, daily goals, timezone-aware reminders, weekly progress, history, saved responses, usage protection, Supabase file storage, and a FastAPI health endpoint for Railway.

There are no instructor-facing workflows and no payment system in this launch MVP.

## Stack

- Telegram Bot API and `python-telegram-bot`
- Python 3.11+ and FastAPI
- OpenAI Responses API
- Supabase PostgreSQL and private Storage
- Railway, Docker, or any comparable Python host

`main.py` defaults to Telegram long polling for local or VM runs. Production Railway currently sets `RUNTIME_MODE=webhook`: FastAPI serves `/health` and the protected `/telegram/webhook` endpoint. The webhook requires `TELEGRAM_WEBHOOK_SECRET` and `INTERNAL_CRON_SECRET`, with either `TELEGRAM_WEBHOOK_BASE_URL` or `RAILWAY_PUBLIC_DOMAIN` for the public origin.

## Project structure

```text
buildwise-ai/
├── bot/
│   ├── handlers/bot_handlers.py       Telegram UX and workflows
│   ├── services/openai_service.py     Text, image, PDF, and quiz AI
│   ├── services/supabase_service.py   Database and private file storage
│   ├── services/reminder_service.py   Timezone-aware notifications
│   ├── api.py                         FastAPI health service
│   ├── runtime.py                     Telegram + FastAPI process
│   ├── webhook_runtime.py             Production Telegram webhook + FastAPI
│   ├── config.py                      Validated environment settings
│   ├── domain.py                      Shared types
│   ├── keyboards.py                   Telegram inline menus
│   ├── prompts.py                     Concise AI behavior and safety
│   └── router.py                      Automatic request detection
├── migrations/001_buildwise_mvp.sql  Supabase schema and database functions
├── docs/                              Architecture and deploy checklist
├── tests/                             Offline unit tests
├── .env.example
├── Dockerfile
├── railway.toml
├── main.py
└── requirements.txt
```

## Before you start

You need:

1. Python 3.11 or newer.
2. A Telegram bot token from [@BotFather](https://t.me/BotFather).
3. An OpenAI API key with API billing enabled.
4. A Supabase project.

ChatGPT subscriptions and OpenAI API billing are separate.

If a Telegram token has ever been pasted into a chat or public place, revoke it in BotFather with `/revoke` and create a new token before deployment.

## 1. Create Supabase data and storage

1. Create or open a Supabase project.
2. Open **SQL Editor**.
3. Copy all of `migrations/001_buildwise_mvp.sql` into the editor and run it once.
4. Confirm the tables appear in **Table Editor** and the private `buildwise-uploads` bucket appears under **Storage**.
5. In **Settings → API Keys → Publishable and secret API keys**, create or copy a server secret key (`sb_secret_...`) and copy the project URL.

The migration enables Row Level Security without public policies. Only the backend uses the revocable Supabase secret key. Never put that key in a mobile app, web page, Telegram message, log, or Git repository. The legacy service-role variable remains accepted temporarily for backward compatibility, but new deployments should use `SUPABASE_SECRET_KEY`.

The private Storage bucket holds question images, building photos, sketches, and PDFs. PostgreSQL stores only their object paths, not large binary data.

## 2. Start locally on Windows PowerShell

Open PowerShell inside the project folder and run each line separately:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Replace the four required placeholders in `.env`. Do not add quotes unless they are part of a value. Save the file, then run:

On Windows, you can instead run `powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1`. It asks for each value privately and writes `.env` without displaying secret text.

```powershell
python main.py
```

Expected result:

- the log says BuildWise is ready;
- `http://127.0.0.1:8000/health` returns `{"status":"ok",...}`;
- `/start` opens the BuildWise home menu in Telegram.

If `pip` is not recognized, use `python -m pip` exactly as shown above.

## 3. Start on macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`, then run `python main.py`.

## Telegram commands

- `/home` — BuildWise home menu and today’s goal
- `/solve [question]` — Study & Solve
- `/practice [topic]` — Quick Practice setup
- `/sketch` — sketch or floor-plan analysis
- `/building` — building-photo analysis
- `/history` — recent conversations and saved items
- `/progress` — seven-day summary
- `/goal [1-100]` — daily study goal
- `/reminders` — Morning, Afternoon, Evening, custom, or Off
- `/timezone Area/City` — local timezone for streaks and reminders
- `/settings` — concise/detailed answers, buttons, and history
- `/mode` — optional manual mode selection
- `/cancel` — return to automatic detection

Users can skip commands and send content directly. A text question goes to Study & Solve, PDFs default to study, and clear building/sketch keywords route their uploads. When an unlabelled image is ambiguous, BuildWise asks whether to solve it, analyze a building, analyze a sketch, or explain it. This avoids a second AI classification call.

## Study tracking behavior

- Opening the bot does not change a streak.
- A solved study question, checked answer, completed practice, quiz, or revision can count.
- Multiple activities on the same local date increase goal progress but not the streak more than once.
- Missing a local calendar day resets the next completed-day streak to 1.
- Milestones are shown at 3, 7, 14, 30, 50, and 100 days.
- Quick Practice supports Easy/Medium/Hard and 3/5/10 multiple-choice questions, then records score and mistakes.

For the repeat-button regression checks and an exact production Telegram test, see [Quick Practice verification](docs/QUICK_PRACTICE_VERIFICATION.md). An unfinished Practice session is held in process memory and must be restarted after a bot deployment or restart.

For the Building student curriculum basis and proposed product priorities, see the [Building student learning roadmap](docs/BUILDING_STUDENT_ROADMAP.md).

User timezone is stored as an IANA name such as `Africa/Lagos`. New profiles use UTC temporarily and are asked once to confirm a timezone.

## Reminders

Reminder preferences are durable in Supabase. A Railway process checks for due reminders every 60 seconds and sends no more than one automatic reminder per local day. “Remind Me Later” schedules a user-requested two-hour snooze. Students can turn reminders off at any time.

Run only one polling process per Telegram token. Production currently uses a webhook; avoid overlapping bot deployments or reminder schedulers unless update and reminder processing are coordinated across replicas.

## Usage and abuse protection

- `DAILY_AI_REQUEST_LIMIT` controls per-user AI calls per local day.
- `REQUEST_COOLDOWN_SECONDS` prevents rapid repeated calls.
- Atomic Supabase functions prevent concurrent daily-limit bypass.
- Telegram update IDs are stored for seven days to ignore duplicate deliveries.
- Failed OpenAI calls are returned to the daily allowance.
- Only supported images and PDFs under `MAX_FILE_SIZE_MB` are accepted.

Set `ALLOWED_USER_IDS` while privately testing. Leave it empty only when ready for public users and API costs.

## Safety

Building Analysis is a preliminary photo review. It never declares a building or element structurally safe, and it advises professional inspection or emergency action when appropriate.

Sketch Analysis never claims construction, code, structural, or permit approval. Measurements and hidden elements are not invented.

These safeguards do not replace product-specific legal review, privacy review, abuse testing, or qualified professional advice.

## Deploy to Railway

1. Use the BuildWise GitHub repository; keep `.env` and all credentials out of Git.
2. In Railway, create a project and deploy that repository.
3. Add required values under the Railway service’s **Variables** tab. For the current production webhook runtime, set `RUNTIME_MODE=webhook`, the two webhook/cron secrets, and a public webhook base URL or Railway public domain. Railway supplies `PORT` automatically.
4. Keep the start command from `railway.toml`: `python main.py`.
5. Deploy and wait for `/health` to pass.
6. Open Telegram and test `/start`, a text question, an image, `/practice`, and `/history`.

`railway.toml` uses Railway configuration-as-code, a `/health` check, and restart-on-failure. Supabase holds durable state, so a Railway restart does not erase profiles, streaks, goals, history, reminders, quiz results, usage, or uploads.

See `docs/DEPLOYMENT_CHECKLIST.md` before launch.

## Deploy on Google Cloud's free VM allowance

For a continuously running long-polling bot, see `docs/GOOGLE_CLOUD_FREE_DEPLOY.md`. The included `deploy/google-vm-setup.sh` installer creates an automatically restarting Linux service. Stay within Google's current eligible region, `e2-micro`, standard-disk, and network limits, and configure a billing alert.

## Docker

```powershell
docker build -t buildwise-ai .
docker run --rm --env-file .env -p 8000:8000 buildwise-ai
```

The architecture is not tied to Railway; the same container can run on a VPS or another container host.

## Tests

Offline tests require no live keys:

```powershell
python -m unittest discover -s tests -v
```

Before production, run the migration in a test Supabase project and perform the live smoke flows in the deployment checklist. External Telegram, OpenAI, Supabase, and reminder delivery cannot be fully verified by offline unit tests alone.

## Common problems

- **`pip` is not recognized:** run `python -m pip install -r requirements.txt`.
- **Configuration error:** confirm `.env` is beside `main.py` and all four required values are real.
- **Supabase startup error:** run the complete SQL migration and verify the project URL/service key.
- **Bot does not reply:** stop any other process using the same Telegram token.
- **Model not found/unauthorized:** verify API billing and `OPENAI_MODEL` access.
- **Reminder does not send:** set `/timezone`, enable `/reminders`, keep one Railway instance running, and verify the bot is not blocked.
- **File rejected:** send a supported image or PDF under the configured size.

**Less Stress. More Solutions. BuildWise. 🚀**
Deployment trigger: 2026-08-24
