# TurnAPI

Browser-backed local API for Consensus. The recommended production-style setup is:

- run a long-lived real browser session
- attach the API to that browser over CDP
- keep the browser headful but hidden/background instead of true headless

## Recommended setup

### 1. Launch Brave in the background with remote debugging

```bash
BROWSER_CHAT_BROWSER_APP="Brave Browser" \
BROWSER_CHAT_WINDOW_MODE=background \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome-local
```

This uses your local Brave profile and keeps the browser available for attached sessions.

### 2. Start the API in attach mode

```bash
BROWSER_CHAT_MODE=attach \
BROWSER_CHAT_CDP_URL=http://127.0.0.1:9444 \
uv run python run.py
```

### 3. Use the API

- Local UI: `http://127.0.0.1:8002/`
- Health check: `GET /health`
- Streaming chat: `POST /chat`
- Sync chat: `POST /chat/sync`

## How session behavior works

- No `session_id`: create a new remote browser tab/session.
- Existing `session_id`: reuse that same remote tab for follow-up messages.
- `New Chat` in the localhost UI clears the stored session id so the next message opens a fresh remote tab.

## Config guide

### Browser process / attachment

`BROWSER_CHAT_MODE`

- `attach`
  Attach to an already-running browser on `BROWSER_CHAT_CDP_URL`.
- `persistent`
  Launch one long-lived Playwright persistent profile from `BROWSER_CHAT_PROFILE_DIR`.
- `ephemeral`
  Launch a throwaway Playwright browser/context. Lowest stability. Mostly for debugging.

Recommended: `attach`

`BROWSER_CHAT_CDP_URL`

- Example: `http://127.0.0.1:9444`
- Used only in `attach` mode.
- Must match the debug port of the browser you started.

`BROWSER_CHAT_PROFILE_DIR`

- Example: `/Users/you/dev/TurnAPI/.browser-profile`
- Used by `persistent` mode for a dedicated long-lived Playwright profile.
- Ignored in `attach` mode.

### Browser app / local profile launcher

`BROWSER_CHAT_BROWSER_APP`

- `Brave Browser`
- `Google Chrome`

Used by `run.py chrome` and `run.py chrome-local` on macOS.

Recommended: `Brave Browser`

`BROWSER_CHAT_CHROME_USER_DATA_DIR`

- Brave default:
  `/Users/<you>/Library/Application Support/BraveSoftware/Brave-Browser`
- Chrome default:
  `/Users/<you>/Library/Application Support/Google/Chrome`

Used by `chrome-local` to point at your real browser profile store.

`BROWSER_CHAT_CHROME_PROFILE_DIRECTORY`

- `Default`
- `Profile 1`
- `Profile 2`
- any existing Chromium profile directory name

Used by `chrome-local` to choose which local profile to boot with.

Recommended: whichever profile is already logged into Consensus.

`BROWSER_CHAT_CDP_PORT`

- Example: `9222`
- Example: `9444`

Used by `run.py chrome` and `run.py chrome-local` when launching the debug browser.

Recommended: `9444` if you want to keep it separate from other local browser automation.

### Window visibility

`BROWSER_CHAT_WINDOW_MODE`

- `background`
  Best default. Keep browser headful but launch it in the background / minimized when possible.
- `offscreen`
  Push the browser window offscreen where supported.
- `visible`
  Normal visible browser window. Best for debugging.
- `minimized`
  Start minimized where supported.

Recommended: `background`

Why: it keeps real-browser behavior without relying on true headless.

### Remote target / scraping

`BROWSER_CHAT_URL`

- Default: `https://consensus.app/search/`
- Change only if the target site/chat entrypoint changes.

`BROWSER_CHAT_INPUT_SELECTORS`

- Comma-separated CSS selectors
- Example:
  `textarea,[contenteditable="true"]`

Used to find the input box on the target site.

`BROWSER_CHAT_SUBMIT_SELECTORS`

- Comma-separated CSS selectors
- Default includes:
  `#search-button,[data-testid="search-button"],button[type="submit"]`

Used to submit the prompt after typing.

`BROWSER_CHAT_RESPONSE_SELECTORS`

- Comma-separated CSS selectors
- Used to find the answer/result block on the remote page.

`BROWSER_CHAT_SOURCE_SELECTORS`

- Comma-separated CSS selectors
- Used to extract links/sources from the remote page.

### Timing / waiting

`BROWSER_CHAT_WAIT_TIMEOUT_SECONDS`

- Example: `90`
- Maximum time the scraper waits for a completed answer.

`BROWSER_CHAT_POLL_INTERVAL_SECONDS`

- Example: `1.0`
- How often the scraper polls the remote page for updated answer content.

`BROWSER_CHAT_STABLE_POLLS`

- Example: `3`
- Number of unchanged polls before the response is treated as finished.

## Common changes

### Use Chrome instead of Brave

```bash
BROWSER_CHAT_BROWSER_APP="Google Chrome" \
BROWSER_CHAT_WINDOW_MODE=background \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome-local
```

Then:

```bash
BROWSER_CHAT_MODE=attach \
BROWSER_CHAT_CDP_URL=http://127.0.0.1:9444 \
uv run python run.py
```

### Use a different local profile

```bash
BROWSER_CHAT_BROWSER_APP="Brave Browser" \
BROWSER_CHAT_CHROME_PROFILE_DIRECTORY="Profile 1" \
BROWSER_CHAT_WINDOW_MODE=background \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome-local
```

### Use a dedicated hidden browser instead of your real profile

```bash
BROWSER_CHAT_BROWSER_APP="Brave Browser" \
BROWSER_CHAT_WINDOW_MODE=background \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome
```

Then attach:

```bash
BROWSER_CHAT_MODE=attach \
BROWSER_CHAT_CDP_URL=http://127.0.0.1:9444 \
uv run python run.py
```

### Debug visibly

```bash
BROWSER_CHAT_WINDOW_MODE=visible \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome-local
```

## Practical advice

- If `chrome-local` or `brave-local` style launch does not expose the debug port, fully quit that browser first.
- `attach` mode is the most faithful because it reuses a real logged-in browser profile.
- `persistent` mode is the next-best option if you want a dedicated automation profile.
- True headless is possible in some paths, but it is less reliable for this target than background headful mode.
