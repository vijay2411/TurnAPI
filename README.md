# TurnAPI

TurnAPI converts a chat interface running inside Chromium into an API.

The primary goal is not the localhost UI. The primary goal is:
- attach to a real browser session
- drive a website chat surface
- wait until the remote answer is fully generated
- expose that interaction through stable HTTP endpoints

The localhost UI is optional and sits on top of the same backend.

## Current status

The codebase currently works as a browser-backed API bridge with:
- session creation
- multi-turn session reuse
- parallel sessions
- stored remote conversation links via `session_link`
- minimal OpenAI-style and Anthropic-style endpoints

The current live-tested target is `consensus.app`, but the runtime is now structured so target-specific logic can be swapped through adapters and selector overrides.

## Recommended runtime model

Recommended for real use:
- run a long-lived Chromium-based browser
- keep it headful but hidden/background instead of true headless
- attach the API to that browser over CDP

This is the most stable model for authenticated sites and sites with anti-bot pressure.

## Run it

### 1. Start a debug browser

If you want to use your real local logged-in profile on macOS:

```bash
BROWSER_CHAT_BROWSER_APP="Brave Browser" \
BROWSER_CHAT_WINDOW_MODE=background \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome-local
```

If you want a dedicated automation profile:

```bash
BROWSER_CHAT_BROWSER_APP="Brave Browser" \
BROWSER_CHAT_WINDOW_MODE=background \
BROWSER_CHAT_CDP_PORT=9444 \
uv run python run.py chrome
```

### 2. Start the API attached to that browser

```bash
BROWSER_CHAT_MODE=attach \
BROWSER_CHAT_CDP_URL=http://127.0.0.1:9444 \
uv run python run.py
```

### 3. Check health

```bash
curl -s http://127.0.0.1:8002/health
```

### 4. Open docs if needed

- Swagger UI: `http://127.0.0.1:8002/docs`
- Local UI: `http://127.0.0.1:8002/`

## API examples

### Minimal bridge API

Create a new remote session and send the first prompt:

```bash
curl -s -X POST http://127.0.0.1:8002/chat/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "What are the effects of caffeine on sleep?"
  }'
```

Continue the same session:

```bash
curl -s -X POST http://127.0.0.1:8002/chat/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Focus only on adolescents",
    "session_id": "abc12345"
  }'
```

Resume from a stored remote conversation URL:

```bash
curl -s -X POST http://127.0.0.1:8002/chat/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Continue this conversation",
    "session_link": "https://consensus.app/search/..."
  }'
```

### OpenAI-style API

```bash
curl -s -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "browser-chat",
    "messages": [
      {"role": "user", "content": "What are the effects of caffeine on sleep?"}
    ]
  }'
```

Streaming:

```bash
curl -N -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "browser-chat",
    "stream": true,
    "messages": [
      {"role": "user", "content": "What are the effects of caffeine on sleep?"}
    ]
  }'
```

### Anthropic-style API

```bash
curl -s -X POST http://127.0.0.1:8002/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "browser-chat",
    "messages": [
      {"role": "user", "content": "What are the effects of caffeine on sleep?"}
    ]
  }'
```

### Session endpoints

Create/open a session without sending a real prompt yet:

```bash
curl -s -X POST http://127.0.0.1:8002/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{
    "target_url": "https://consensus.app/search/"
  }'
```

List active in-memory sessions:

```bash
curl -s http://127.0.0.1:8002/v1/sessions
```

Inspect one session:

```bash
curl -s http://127.0.0.1:8002/v1/sessions/abc12345
```

## Session model

- No `session_id`: create a new managed tab.
- `session_id`: continue the same active managed tab.
- `session_link`: reopen the remote conversation URL in a managed tab and continue from there.
- Multiple sessions can run in parallel up to the configured session limit.

## Main config

### Browser control

`BROWSER_CHAT_MODE`
- `attach`
- `persistent`
- `ephemeral`

Recommended: `attach`

`BROWSER_CHAT_CDP_URL`
- Example: `http://127.0.0.1:9444`

`BROWSER_CHAT_PROFILE_DIR`
- Used by `persistent`

`BROWSER_CHAT_WINDOW_MODE`
- `background`
- `offscreen`
- `visible`
- `minimized`

Recommended: `background`

### Browser app / profile

`BROWSER_CHAT_BROWSER_APP`
- `Brave Browser`
- `Google Chrome`

`BROWSER_CHAT_CHROME_USER_DATA_DIR`
- Chromium user-data root

`BROWSER_CHAT_CHROME_PROFILE_DIRECTORY`
- `Default`
- `Profile 1`
- `Profile 2`

`BROWSER_CHAT_CDP_PORT`
- Example: `9444`

### Target-specific selector overrides

`BROWSER_CHAT_URL`
- default target URL

`BROWSER_CHAT_INPUT_SELECTORS`
- comma-separated selectors for the chat box

`BROWSER_CHAT_SUBMIT_SELECTORS`
- comma-separated selectors for the send button

`BROWSER_CHAT_RESPONSE_SELECTORS`
- comma-separated selectors for answer extraction

`BROWSER_CHAT_SOURCE_SELECTORS`
- comma-separated selectors for source extraction

### Waiting / completion

`BROWSER_CHAT_WAIT_TIMEOUT_SECONDS`
- maximum wait for a finished answer

`BROWSER_CHAT_POLL_INTERVAL_SECONDS`
- how frequently the page is scraped

`BROWSER_CHAT_STABLE_POLLS`
- how many unchanged polls are required before treating the answer as complete

## Linux note

The project purpose is Linux Chromium automation. The core runtime is already Chromium/CDP-based, but the helper launcher in `run.py` is still most polished for macOS local-profile launching. On Linux, the recommended pattern is still the same:
- launch Chromium/Chrome manually with remote debugging
- point `BROWSER_CHAT_CDP_URL` at it
- run TurnAPI in `attach` mode

## Important limitations

- Sites with auth walls still require a valid logged-in browser session.
- True headless mode is less reliable than hidden/background headful mode.
- Selector tuning is still target-specific.
- Session state is in memory; restarting the API drops `session_id` mappings, though `session_link` can be reused later.
- Source extraction is generic and may need target-specific improvement.
- Anti-bot protections can still block or degrade automation depending on the site.

## Code map

- Core target abstraction: [consensus_api.py](./consensus_api.py)
- Browser session management: [consensus_api.py](./consensus_api.py)
- API endpoints: [consensus_api.py](./consensus_api.py)
- Launcher helper: [run.py](./run.py)
- Tests: [test_consensus_api.py](./test_consensus_api.py)
- Project operating docs: [project.md](./project.md), [agent.md](./agent.md), [techniques.md](./techniques.md), [tasks.md](./tasks.md)
