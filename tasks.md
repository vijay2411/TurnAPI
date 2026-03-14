# Tasks

This file records what has been attempted and what has been completed so far. It is both a history log and a checklist template for future site integrations.

## Completed work so far

### 1. Initial browser-backed bridge

- Built a FastAPI service that drives a remote browser page and scrapes chat output.
- Added `/chat` SSE streaming and `/chat/sync` synchronous response endpoints.

Status:
- completed

### 2. Session-based browser model

- Added session creation and reuse through `session_id`.
- Established the rule:
  - new request without `session_id` => new remote tab/session
  - request with `session_id` => reuse same remote tab/session

Status:
- completed

### 3. Browser mode support

- Added support for:
  - `attach`
  - `persistent`
  - `ephemeral`
- Added browser profile and CDP configuration.

Status:
- completed

### 4. Hidden/background browser operation

- Added window mode support:
  - `background`
  - `offscreen`
  - `visible`
  - `minimized`
- Established background headful mode as the preferred runtime.

Status:
- completed

### 5. Real-site live debugging on Consensus

- Tested fresh sessions against the real site.
- Observed:
  - Cloudflare/security interstitials
  - landing page extraction failures
  - sign-up redirects
- Verified that the real blocker was page/session state, not just browser launch.

Status:
- completed

### 6. Submit behavior fix

- Verified that typing alone was not enough.
- Added explicit submit-button clicking.
- Kept Enter key as fallback.

Status:
- completed

### 7. Input behavior fix

- Changed from `fill()`-only interaction to typed input with delay for better live reliability.

Status:
- completed

### 8. Response extraction fix

- Stopped returning early placeholder content.
- Added heuristics to ignore skeleton/invalid output.
- Preferred the latest response block over stale prior-turn blocks.

Status:
- completed

### 9. Completion detection

- Implemented contiguous polling until output stabilizes.
- Added stable-poll logic so the API waits until the remote answer is fully generated.

Status:
- completed

### 10. Follow-up message support in same session

- Verified at least two live follow-up turns in the same session.
- Confirmed same-tab reuse and changing remote conversation state.

Status:
- completed

### 11. Localhost UI support

- Upgraded `/` into a session-aware local chat client.
- Added local follow-up support and `New Chat`.

Status:
- completed

### 12. Auth/block detection

- Added detection for:
  - Cloudflare/security interstitials
  - sign-in/sign-up walls
- Stopped pretending success when the browser had not reached the chat UI.

Status:
- completed

### 13. Attach-to-real-browser workflow

- Tested and verified attach mode against a live Brave/Chromium profile.
- Established attach mode as the recommended runtime.

Status:
- completed

### 14. Generic target adapter extraction

- Moved site-specific logic out of the generic scraper/runtime path.
- Introduced:
  - `BrowserTargetAdapter`
  - `ConsensusTargetAdapter`
  - `GenericTargetAdapter`

Status:
- completed

### 15. Provider-style API surface

- Added:
  - `/v1/sessions`
  - `/v1/sessions/{session_id}`
  - `/v1/chat/completions`
  - `/v1/messages`
- Added OpenAI-style and Anthropic-style minimal compatibility payloads.

Status:
- completed

### 16. Stored remote link resume

- Added `session_link` support so a known remote conversation URL can be used to resume/reopen a chat later.

Status:
- completed

### 17. Tests and verification coverage

- Added/updated unit tests for:
  - adapter selection
  - session handling
  - auth/block detection
  - endpoint compatibility layers
  - sync/stream behavior

Status:
- completed

## Current supported features

- browser-backed chat automation
- new sessions
- continued sessions by `session_id`
- resumed sessions by remote `session_link`
- parallel sessions
- asynchronous wait until output is complete
- SSE streaming
- OpenAI-style compatibility
- Anthropic-style compatibility
- optional local UI

## Known gaps

- durable session persistence beyond process memory
- formal site registry / adapter package layout
- Linux-specific launcher polish
- structured source extraction for every target
- adapter onboarding automation

## Repeatable checklist for a new website

Use this when onboarding another site:

1. Reach the chat UI in a real Chromium session.
2. Verify attach mode works.
3. Identify:
   - input selector
   - submit selector
   - response selector
   - source selector
4. Identify blocked/auth states.
5. Identify placeholder/loading states.
6. Verify:
   - first message
   - at least two follow-ups
   - multiple sessions in parallel
   - stored-link resume if available
7. Encode the site in an adapter.
8. Add tests.
9. Update docs.
10. Commit the feature.

## Notes for the next phase

The next stage of this project is not “make Consensus nicer.” It is:
- repeat the adapter workflow for additional sites
- make Linux Chromium the primary operational path
- keep the public API stable while adding new target adapters
