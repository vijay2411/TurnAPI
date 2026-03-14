# Techniques

This file records techniques, tools, and approaches tried in this project. A failed attempt is still useful. “Did not work” means “not the current recommended path,” not “never try again.”

## Format

- Technique / tool
- What it was used for
- Result
- What worked
- What failed or was brittle
- Last tried

## Playwright

- Used for:
  browser control, page navigation, selector probing, input, clicking, scraping, attach mode, persistent contexts
- Result:
  current primary runtime
- What worked:
  - attaching to a real Chromium/Brave session over CDP
  - persistent browser contexts
  - typed input plus explicit submit-button click
  - polling DOM until the answer stabilized
  - session-per-tab model
  - provider-style API surface on top of scraper output
- What failed or was brittle:
  - fresh profiles on protected sites
  - true headless reliability on the live target
  - persistent profile launch when profile lock already exists
- Last tried:
  2026-03-15

## Nodriver

- Used for:
  alternative browser driver with lower automation fingerprinting
- Result:
  supported in code, not the primary recommended runtime
- What worked:
  - basic support path exists in runtime
  - can be useful as an alternative driver for some sites
- What failed or was brittle:
  - did not eliminate the real blockers by itself
  - fresh sessions still had state/trust problems
  - not the path that produced the stable live result here
- Last tried:
  2026-03-14

## Attach mode over CDP

- Used for:
  reusing a real browser session that is already trusted/logged in
- Result:
  current recommended setup
- What worked:
  - bypassed the instability of launching a fresh browser for every request
  - reused auth state
  - allowed same-session follow-ups in one tab
  - best live behavior on Brave/Chromium
- What failed or was brittle:
  - depends on debug port availability
  - local profile launch can fail if the browser is already using that profile
- Last tried:
  2026-03-15

## Persistent Playwright profile

- Used for:
  dedicated automation profile without attaching to a user browser
- Result:
  secondary option
- What worked:
  - good fallback when attach mode is not available
  - keeps browser state across requests
- What failed or was brittle:
  - profile locking errors
  - less realistic than a real user-driven logged-in browser
- Last tried:
  2026-03-15

## Ephemeral browser sessions

- Used for:
  quick isolated runs
- Result:
  supported but not recommended
- What worked:
  - simple for debugging generic browser flows
- What failed or was brittle:
  - poor stability on protected/authenticated sites
  - no warm state, no retained trust, no retained login
- Last tried:
  2026-03-14

## Hidden/background headful browser

- Used for:
  keeping browser behavior realistic without showing the window constantly
- Result:
  recommended over true headless
- What worked:
  - better fidelity than headless
  - good balance between usability and realism
- What failed or was brittle:
  - still requires a stable browser/profile setup
  - helper launching is currently more polished on macOS than Linux
- Last tried:
  2026-03-15

## True headless mode

- Used for:
  fully invisible automation
- Result:
  not recommended for the current live target
- What worked:
  - technically possible in some flows
- What failed or was brittle:
  - more detection pressure
  - less reliable page behavior on the live target
- Last tried:
  2026-03-14

## Enter key submission

- Used for:
  sending prompts after filling the chat box
- Result:
  insufficient as the main submission strategy
- What worked:
  - okay as fallback on some chat UIs
- What failed or was brittle:
  - the live target often required clicking the real submit button
- Last tried:
  2026-03-14

## Explicit submit-button click

- Used for:
  reliable prompt submission
- Result:
  required for the live target
- What worked:
  - clicking selectors like `#search-button`
  - checking disabled state before clicking
- What failed or was brittle:
  - requires site-specific selector knowledge
- Last tried:
  2026-03-15

## `fill()` only input

- Used for:
  setting textarea values quickly
- Result:
  less reliable than typed input on the live target
- What worked:
  - simple fields on generic pages
- What failed or was brittle:
  - some chat surfaces responded better to `type(..., delay=...)`
- Last tried:
  2026-03-14

## Typed input with delay

- Used for:
  making the chat box react like a real user input sequence
- Result:
  recommended for current live target
- What worked:
  - better interaction with the real chat UI
- What failed or was brittle:
  - slower than raw fill
- Last tried:
  2026-03-15

## Contiguous scraper / stable-poll completion detection

- Used for:
  waiting until the remote answer is fully generated
- Result:
  core technique used now
- What worked:
  - compare current extracted answer vs prior answer
  - ignore placeholder output
  - wait until the response stops changing for N polls
  - prefer the latest response node for follow-ups
- What failed or was brittle:
  - requires target-specific heuristics to avoid stale or placeholder nodes
- Last tried:
  2026-03-15

## Stored remote conversation links

- Used for:
  resuming past conversations later
- Result:
  supported as `session_link`
- What worked:
  - useful when the site exposes a stable per-conversation URL
- What failed or was brittle:
  - not all sites provide stable conversation URLs
  - some sites may require additional local state even with the URL
- Last tried:
  2026-03-15

## Cloudflare / auth-wall handling

- Used for:
  recognizing when the browser is not actually on the chat UI
- Result:
  detection added; bypass is not the strategy
- What worked:
  - detecting interstitial/security pages
  - detecting sign-in/sign-up redirects
  - switching to a trusted attached browser session
- What failed or was brittle:
  - fresh sessions on protected sites
  - assuming the page had reached the chat surface when it had not
- Last tried:
  2026-03-15

## Localhost UI

- Used for:
  manual debugging and demoing the bridge
- Result:
  useful add-on, not primary product
- What worked:
  - session-aware local chat testing
  - follow-up message debugging
- What failed or was brittle:
  - originally returned too early before remote completion logic was fixed
- Last tried:
  2026-03-15
