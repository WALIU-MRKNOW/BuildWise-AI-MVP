# BuildWise WebMCP proof of concept

BuildWise WebMCP adds an optional browser interface to the existing BuildWise backend without replacing the Telegram bot.

Architecture:

```text
Telegram ──┐
           ├──> BuildWise backend/services ──> OpenAI + Supabase
WebMCP ────┘
```

The first site tool is `generate_practice_quiz`. It calls the existing `OpenAIService.generate_quiz()` implementation used by Telegram Quick Practice, so BuildWise has one quiz-generation engine.

## Current WebMCP API

This POC follows the current WebMCP draft API and registers the tool in browser JavaScript with:

```js
await document.modelContext.registerTool({
  name: "generate_practice_quiz",
  description: "...",
  inputSchema: { /* JSON Schema */ },
  execute: async (input, { signal }) => { /* same-origin backend call */ }
});
```

WebMCP is a browser/site-tool API, not a separate HTTP MCP server. ChatGPT/Codex discovers the registered tool while `/app` is open in a supported built-in browser.

References checked for this implementation:

- WebMCP Community Group draft: https://webmachinelearning.github.io/webmcp/
- OpenAI WebMCP Challenge: https://openai.com/webmcp-challenge/
- OpenAI site-tools help: https://help.openai.com/en/articles/20001423-using-site-tools-in-the-chatgpt-desktop-app

## Security model

`WEBMCP_ENABLED` defaults to `false`, so existing production behavior is unchanged until WebMCP is explicitly enabled.

The preview uses:

- a server-side preview access key entered at `/app`
- a signed `HttpOnly`, `Secure`, `SameSite=Strict` session cookie
- a same-origin CSRF token for tool POST requests
- Pydantic validation on every tool input
- service-role-only Supabase access from Python
- no OpenAI key, Supabase secret, Telegram token, preview access key, or session signing secret in browser JavaScript
- a durable daily WebMCP usage counter with exact reservation refunds on AI failure
- server-side audit logs that identify the tool and an abbreviated non-reversible subject hash, not credentials

The generated quiz content is treated as untrusted model output and the WebMCP tool advertises `untrustedContentHint: true`.

## Required environment variables

Existing BuildWise variables remain required because this POC runs inside the existing BuildWise service.

Additional variables:

```text
WEBMCP_ENABLED=true
WEBMCP_ACCESS_KEY=<random preview access key, at least 24 characters>
WEBMCP_SESSION_SECRET=<random signing secret, at least 32 characters>
WEBMCP_SESSION_TTL_SECONDS=28800
WEBMCP_DAILY_REQUEST_LIMIT=20   # optional; empty inherits DAILY_AI_REQUEST_LIMIT
```

Before enabling the preview, apply:

```text
migrations/005_webmcp_usage.sql
```

to the Supabase project used by that preview environment.

Do not put any real secret in committed files or browser code.

## Run locally

1. Create a virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` and provide normal BuildWise secrets.
3. Apply migrations through `005_webmcp_usage.sql` to a development Supabase project.
4. Set the WebMCP variables above.
5. Run `python main.py`.
6. Open `http://localhost:8000/app` for ordinary browser inspection. Secure cookies require HTTPS for a real authenticated browser test, so use an HTTPS local tunnel or preview deployment for end-to-end WebMCP testing.

For a deployed preview, open `/app`, enter the preview access key, then keep that page open in ChatGPT's built-in browser.

## Test the tool

In a supported ChatGPT/Codex browser, open the authenticated `/app` page and ask:

> Use BuildWise to generate a 5-question practice quiz about reinforced concrete.

Expected flow:

```text
Student request
  -> ChatGPT/Codex discovers generate_practice_quiz
  -> WebMCP execute callback
  -> POST /app/api/tools/generate-practice-quiz
  -> server auth + CSRF + validation + usage reservation
  -> existing BuildWise OpenAIService.generate_quiz()
  -> structured quiz JSON
  -> agent + page receive the same useful result
```

The tool input schema accepts:

- `topic`: 2-200 characters
- `difficulty`: `easy`, `medium`, or `hard`
- `count`: 1-10

The output contains the topic, difficulty, questions/options/correct answers/explanations, and current preview usage.

## Adding future tools

Add future WebMCP tools in the isolated `bot/webmcp/` package. Keep the browser tool thin: validate inputs, call a same-origin protected backend endpoint, and reuse an existing BuildWise service whenever possible.

Planned candidates include:

- `analyze_floor_plan`
- `analyze_building_image`
- `ask_pdf`
- `ask_course_question`
- `get_referral_link`
- `submit_support_ticket`
- `get_usage_summary`

For tools that upload files or mutate user data, define explicit authorization, size/content limits, confirmation semantics, CSRF handling, and structured errors before registering them.

## Production note

This POC should first be deployed to a preview environment with `WEBMCP_ENABLED=true`. The live Telegram deployment can keep `WEBMCP_ENABLED=false`; no Telegram handler, AI image/PDF path, referral logic, support flow, or admin route needs to change for the preview to remain isolated.
