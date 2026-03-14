# Project Purpose

## Mission

This project exists to convert any chat-based interface on any website into an API, using Chromium automation as the transport layer.

The target outcome is plug-and-play browser-backed API access:
- identify the chat box
- identify the submit action
- identify the response area
- wait until the response is fully generated
- return that response through a stable HTTP API

The optional localhost chat UI is an add-on. It is not the primary product.

The primary product is:
- browser automation
- session management
- response completion detection
- API compatibility surfaces

## What “done” means for a new website

A website is considered integrated when TurnAPI can:
- start a new chat session
- continue an existing session by `session_id`
- resume a previous conversation by stored remote link when possible
- run multiple sessions in parallel
- wait asynchronously until a full response is generated
- return the final response through the API
- optionally stream deltas while polling

## Current architecture

### 1. Target adapter layer

The codebase now separates site-specific logic from the generic runtime.

`BrowserTargetAdapter` owns the website-specific parts:
- target matching
- default URL
- input selectors
- submit selectors
- response selectors
- source selectors
- blocked-page markers
- auth-wall markers
- response filtering heuristics
- same-turn detection heuristics

Current adapters:
- `ConsensusTargetAdapter`
- `ZAITargetAdapter`
- `GenericTargetAdapter`

This is the key abstraction for taking the same process to other websites.

### 2. Request/profile layer

`ChatRequest` and `BrowserChatProfile` carry:
- prompt
- session identity
- stored remote session link
- target URL
- selector overrides
- wait / polling parameters

This layer converts API requests into a fully-resolved runtime profile.

### 3. Session/runtime layer

`SessionManager` owns:
- active browser-backed sessions
- creation of new tabs/pages
- reuse of old sessions
- browser driver selection
- attach / persistent / ephemeral modes
- cleanup

Important behavior:
- no `session_id` means new session
- `session_id` means continue existing managed session
- `session_link` is treated as a target URL for resuming a known remote conversation

### 4. Scraping/orchestration layer

`BrowserChatScraper` owns:
- entering text
- submitting prompts
- polling for updated output
- detecting new-vs-old answers
- detecting completion by stability
- extracting content and sources
- raising blocking/auth errors

This is the engine that turns a browser conversation into API output.

### 5. API layer

The current API surface has three levels:

- low-level bridge
  - `/chat`
  - `/chat/sync`

- session API
  - `/v1/sessions`
  - `/v1/sessions/{session_id}`

- provider-style APIs
  - `/v1/chat/completions`
  - `/v1/messages`

The provider-style endpoints are intentionally minimal and modeled after OpenAI/Anthropic patterns.

### 6. Optional local UI

The root page `/` is a local browser chat client backed by the same API. It is useful for manual testing and debugging, but it is not the core purpose of the project.

## What is specific to the current integrated targets

### Consensus

Consensus-specific behavior currently includes:
- URL matching
- `#search-button` and related submit selectors
- auth-wall markers such as sign-up/sign-in pages
- response skip markers like broken summary states
- selector priorities tuned to Consensus result containers

These belong in the adapter layer and should not leak back into the generic runtime.

### Z.ai

Z.ai-specific behavior currently includes:
- URL matching for `chat.z.ai` / `z.ai`
- `textarea` input discovery on the real app page
- `#send-message-button` submission
- exclusion of `thinking-chain-container` from final answer extraction
- support for short final answers like `RED`, `CYAN`, or `FOAM`
- preference for reusing an already-open matching conversation tab in attach mode when resuming by `session_link`

These also belong in the adapter/session boundary and should not leak into the generic runtime.

## Current limitations

### Runtime limitations

- Sessions are tracked in memory. If the API restarts, `session_id` mappings are lost.
- `session_link` can often recover a conversation, but only if the target site’s URL model supports it.
- Attach mode depends on an already-running browser with a valid CDP endpoint.
- Persistent mode can fail when the profile directory is already locked by another browser process.

### Website limitations

- Many sites require login and a real trusted browser profile.
- Some sites block automation or degrade behavior under headless mode.
- Every website still needs selector discovery and response completion tuning.
- Some chat UIs do not expose a stable per-conversation URL.

### Product limitations

- There is no durable session database yet.
- There is no site-integration registry yet.
- There is no adapter generation workflow yet.
- Streaming is scraper-driven delta streaming, not true provider-native token streaming.

## Replication process for new sites

The intended repeatable procedure is:

1. Reach the real chat UI in a stable Chromium session.
2. Verify the site can be operated reliably in attach mode.
3. Identify selectors for:
   - input
   - submit
   - response
   - source links
4. Detect blocking states and auth walls.
5. Learn how to recognize:
   - placeholder output
   - stale previous-turn output
   - fully completed output
6. Encode those rules in a target adapter.
7. Verify:
   - first turn
   - at least two follow-ups
   - parallel sessions
   - session-link resume if supported
8. Update all project docs after the feature lands.

## Definition of quality

A change is not complete unless:
- it works live or is explicitly marked unverified
- tests are updated
- docs are updated
- the operational learning is preserved in markdown

This repo is meant to accumulate procedure, not just code.
