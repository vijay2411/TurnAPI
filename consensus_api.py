# consensus_api.py
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from rich.console import Console
from rich.logging import RichHandler
import uvicorn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)],
)
logger = logging.getLogger("consensus_api")
console = Console()


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


DEFAULT_TARGET_URL = os.getenv("BROWSER_CHAT_URL", "https://consensus.app/search/")
DEFAULT_INPUT_SELECTORS = _csv_env(
    "BROWSER_CHAT_INPUT_SELECTORS",
    [
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="Message"]',
        'textarea',
        'input[placeholder*="Ask"]',
        'input[placeholder*="Message"]',
        'div[contenteditable="true"]',
    ],
)
DEFAULT_RESPONSE_SELECTORS = _csv_env(
    "BROWSER_CHAT_RESPONSE_SELECTORS",
    [
        'main .prose',
        '.prose',
        '[data-testid="search-results-list"]',
        '[data-testid="search-result"]',
        '[data-testid="drawer-content"]',
        '[data-message-author-role="assistant"]',
        '[data-testid*="assistant"]',
        'main article',
        '[class*="response"]',
        '[class*="answer"]',
        'article',
    ],
)
DEFAULT_SOURCE_SELECTORS = _csv_env(
    "BROWSER_CHAT_SOURCE_SELECTORS",
    [
        '[data-testid="search-result"] a[href^="http"]',
        '[data-testid="drawer-content"] a[href^="http"]',
        'a[href*="doi.org"]',
        'a[href*="pubmed"]',
        'a[href*="ncbi.nlm.nih.gov"]',
        'a[href^="http"]',
    ],
)
DEFAULT_SUBMIT_SELECTORS = _csv_env(
    "BROWSER_CHAT_SUBMIT_SELECTORS",
    [
        "#search-button",
        '[data-testid="search-button"]',
        'button[type="submit"]',
        'button[aria-label*="Submit"]',
        'button[aria-label*="Search"]',
    ],
)

DEFAULT_WAIT_TIMEOUT_SECONDS = int(os.getenv("BROWSER_CHAT_WAIT_TIMEOUT_SECONDS", "90"))
DEFAULT_POLL_INTERVAL_SECONDS = float(os.getenv("BROWSER_CHAT_POLL_INTERVAL_SECONDS", "1.0"))
DEFAULT_STABLE_POLLS = int(os.getenv("BROWSER_CHAT_STABLE_POLLS", "3"))
DEFAULT_BROWSER_DRIVER = os.getenv("BROWSER_CHAT_DRIVER", "playwright").strip().lower()
DEFAULT_BROWSER_MODE = os.getenv("BROWSER_CHAT_MODE", "persistent").strip().lower()
DEFAULT_BROWSER_PROFILE_DIR = str(
    Path(os.getenv("BROWSER_CHAT_PROFILE_DIR", Path(__file__).resolve().parent / ".browser-profile")).resolve()
)
DEFAULT_BROWSER_CDP_URL = os.getenv("BROWSER_CHAT_CDP_URL", "http://127.0.0.1:9222").strip()

try:
    import nodriver as uc

    NODRIVER_AVAILABLE = True
except ImportError:
    uc = None
    NODRIVER_AVAILABLE = False
    logger.warning("Nodriver not installed, using Playwright when available")

try:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PlaywrightError = Exception
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed")


class ChatRequest(BaseModel):
    message: str = Field(..., description="Message to send to the remote browser chat")
    session_id: str | None = None
    stream: bool = True
    headless: bool = Field(default=False, description="Run the browser invisibly")
    solve_captcha: bool = True
    target_url: str | None = Field(
        default=None,
        description="Browser URL containing the chat interface to mirror locally",
    )
    input_selectors: list[str] | None = Field(
        default=None,
        description="Ordered selectors used to find the browser chat input",
    )
    response_selectors: list[str] | None = Field(
        default=None,
        description="Ordered selectors used to extract assistant responses from the page",
    )
    source_selectors: list[str] | None = Field(
        default=None,
        description="Selectors used to extract source links from the chat area",
    )
    submit_selectors: list[str] | None = Field(
        default=None,
        description="Ordered selectors used to submit the chat form after the input is filled",
    )


class ChatResponse(BaseModel):
    session_id: str
    response: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class BrowserChatProfile:
    target_url: str
    input_selectors: list[str]
    response_selectors: list[str]
    source_selectors: list[str]
    submit_selectors: list[str]
    timeout_seconds: int = DEFAULT_WAIT_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    stable_polls: int = DEFAULT_STABLE_POLLS

    @classmethod
    def from_request(cls, request: ChatRequest) -> "BrowserChatProfile":
        return cls(
            target_url=request.target_url or DEFAULT_TARGET_URL,
            input_selectors=request.input_selectors or DEFAULT_INPUT_SELECTORS,
            response_selectors=request.response_selectors or DEFAULT_RESPONSE_SELECTORS,
            source_selectors=request.source_selectors or DEFAULT_SOURCE_SELECTORS,
            submit_selectors=request.submit_selectors or DEFAULT_SUBMIT_SELECTORS,
        )


@dataclass
class BrowserTab:
    session_id: str
    page: Any
    browser_type: str
    browser: Any = None
    browser_context: Any = None
    target_url: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)
    message_count: int = 0
    last_response_text: str = ""
    in_use: bool = False
    owns_page: bool = True
    owns_context: bool = False
    owns_browser: bool = False

    def touch(self) -> None:
        self.last_used = datetime.now()
        self.message_count += 1


class SessionManager:
    def __init__(self, max_sessions: int = 3):
        self.max_sessions = max_sessions
        self.sessions: dict[str, BrowserTab] = {}
        self.lock = asyncio.Lock()
        self._playwright = None
        self._playwright_browser = None
        self._playwright_context = None

    async def get_session(
        self,
        request_session_id: str | None = None,
        headless: bool = False,
        profile: BrowserChatProfile | None = None,
    ) -> BrowserTab:
        profile = profile or BrowserChatProfile(
            target_url=DEFAULT_TARGET_URL,
            input_selectors=DEFAULT_INPUT_SELECTORS,
            response_selectors=DEFAULT_RESPONSE_SELECTORS,
            source_selectors=DEFAULT_SOURCE_SELECTORS,
            submit_selectors=DEFAULT_SUBMIT_SELECTORS,
        )

        async with self.lock:
            if request_session_id:
                existing = self.sessions.get(request_session_id)
                if existing:
                    if existing.in_use:
                        raise HTTPException(409, f"Session {request_session_id} is already in use")
                    if not await self._is_session_alive(existing):
                        await self._close_tab(existing)
                        del self.sessions[request_session_id]
                    else:
                        await self._ensure_target(existing, profile.target_url)
                        existing.in_use = True
                        existing.touch()
                        logger.info("Reusing requested session %s", request_session_id[:8])
                        return existing

                if len(self.sessions) < self.max_sessions:
                    return await self._create_session(headless, profile, request_session_id)

                raise HTTPException(503, "Max sessions reached. Try again in 30s.")

            dead_sids: list[str] = []
            for sid, tab in self.sessions.items():
                if await self._is_session_alive(tab):
                    continue
                dead_sids.append(sid)

            for sid in dead_sids:
                await self._close_tab(self.sessions[sid])
                del self.sessions[sid]

            if len(self.sessions) < self.max_sessions:
                return await self._create_session(headless, profile)

            raise HTTPException(503, "Max sessions reached. Provide an existing session_id or wait for cleanup.")

    async def _create_session(
        self,
        headless: bool,
        profile: BrowserChatProfile,
        preferred_session_id: str | None = None,
    ) -> BrowserTab:
        sid = preferred_session_id or str(uuid.uuid4())[:8]
        logger.info("Creating new session %s", sid[:8])

        if self._should_use_nodriver():
            browser = await uc.start(headless=headless)
            try:
                page = await asyncio.wait_for(browser.get(profile.target_url), timeout=30)
            except asyncio.TimeoutError as exc:
                raise HTTPException(504, "Timeout loading target chat page") from exc
            await asyncio.sleep(2)
            tab = BrowserTab(
                session_id=sid,
                page=page,
                browser=browser,
                browser_type="nodriver",
                target_url=profile.target_url,
                in_use=True,
            )
        else:
            if not PLAYWRIGHT_AVAILABLE:
                raise HTTPException(503, "No supported browser driver is installed")
            tab = await self._create_playwright_session(sid, headless, profile.target_url)

        self.sessions[sid] = tab
        return tab

    async def _create_playwright_session(self, sid: str, headless: bool, target_url: str) -> BrowserTab:
        if not self._playwright:
            self._playwright = await async_playwright().start()

        if DEFAULT_BROWSER_MODE == "attach":
            context = await self._get_or_attach_playwright_context()
            page = await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded")
            return BrowserTab(
                session_id=sid,
                page=page,
                browser=self._playwright_browser,
                browser_context=context,
                browser_type="playwright",
                target_url=target_url,
                in_use=True,
                owns_page=True,
                owns_context=False,
                owns_browser=False,
            )

        if DEFAULT_BROWSER_MODE == "ephemeral":
            browser = await self._playwright.chromium.launch(
                headless=headless,
                args=self._playwright_launch_args(),
            )
            context = await browser.new_context(**self._playwright_context_kwargs())
            await self._add_stealth_script(context)
            page = await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded")
            return BrowserTab(
                session_id=sid,
                page=page,
                browser=browser,
                browser_context=context,
                browser_type="playwright",
                target_url=target_url,
                in_use=True,
                owns_page=True,
                owns_context=True,
                owns_browser=True,
            )

        context = await self._get_or_create_persistent_playwright_context(headless=headless)
        page = await context.new_page()
        await page.goto(target_url, wait_until="domcontentloaded")

        return BrowserTab(
            session_id=sid,
            page=page,
            browser=context.browser,
            browser_context=context,
            browser_type="playwright",
            target_url=target_url,
            in_use=True,
            owns_page=True,
            owns_context=False,
            owns_browser=False,
        )

    async def _get_or_create_persistent_playwright_context(self, headless: bool):
        if self._playwright_context:
            return self._playwright_context

        Path(DEFAULT_BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        self._playwright_context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=DEFAULT_BROWSER_PROFILE_DIR,
            headless=headless,
            args=self._playwright_launch_args(),
            **self._playwright_context_kwargs(),
        )
        await self._add_stealth_script(self._playwright_context)
        return self._playwright_context

    async def _get_or_attach_playwright_context(self):
        if not self._playwright_browser:
            self._playwright_browser = await self._playwright.chromium.connect_over_cdp(DEFAULT_BROWSER_CDP_URL)
        if not self._playwright_browser.contexts:
            raise HTTPException(
                503,
                "Attached browser has no available context. Open Chrome with remote debugging and a normal profile first.",
            )
        return self._playwright_browser.contexts[0]

    def _playwright_launch_args(self) -> list[str]:
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size=1920,1080",
        ]

    def _playwright_context_kwargs(self) -> dict[str, Any]:
        return {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }

    async def _add_stealth_script(self, context: Any) -> None:
        await context.add_init_script(
            """
            delete navigator.__proto__.webdriver;
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            """
        )

    def _should_use_nodriver(self) -> bool:
        if DEFAULT_BROWSER_DRIVER == "nodriver":
            return NODRIVER_AVAILABLE
        if DEFAULT_BROWSER_DRIVER == "playwright":
            return False
        return NODRIVER_AVAILABLE and not PLAYWRIGHT_AVAILABLE

    async def _ensure_target(self, tab: BrowserTab, target_url: str) -> None:
        if tab.target_url == target_url:
            return
        logger.info("Navigating session %s to %s", tab.session_id[:8], target_url)
        if tab.browser_type == "playwright":
            await tab.page.goto(target_url, wait_until="domcontentloaded")
        else:
            tab.page = await tab.browser.get(target_url)
            await asyncio.sleep(2)
        tab.target_url = target_url

    async def _is_session_alive(self, tab: BrowserTab) -> bool:
        try:
            await tab.page.evaluate("1+1")
            return True
        except Exception:
            return False

    async def _close_tab(self, tab: BrowserTab) -> None:
        try:
            if tab.browser_type == "playwright":
                if tab.owns_page:
                    await tab.page.close()
                if tab.owns_context and tab.browser_context:
                    await tab.browser_context.close()
                elif tab.owns_browser and tab.browser:
                    await tab.browser.close()
            else:
                close = getattr(tab.page, "close", None)
                if close:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
        except Exception:
            logger.debug("Failed to close tab %s", tab.session_id, exc_info=True)

    def release(self, session_id: str) -> None:
        tab = self.sessions.get(session_id)
        if tab:
            tab.in_use = False

    async def cleanup(self) -> None:
        for tab in list(self.sessions.values()):
            await self._close_tab(tab)
        self.sessions.clear()
        if self._playwright_context:
            await self._playwright_context.close()
            self._playwright_context = None
        self._playwright_browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


class CaptchaHandler:
    async def check_and_solve(self, page: Any, browser_type: str) -> bool:
        title, body_text, current_url = await self._read_page_state(page, browser_type)
        lowered = f"{current_url}\n{title}\n{body_text}".lower()
        if not self._looks_like_cloudflare_challenge(lowered):
            return True

        selectors = [
            ".cf-turnstile",
            'iframe[src*="turnstile"]',
            'iframe[title*="Cloudflare security challenge"]',
            '[name="cf-turnstile-response"]',
        ]
        try:
            for selector in selectors:
                elem = await self._query(page, browser_type, selector)
                if not elem:
                    continue
                logger.info("CAPTCHA detected, attempting to clear it")
                await asyncio.sleep(0.5 + (time.time() % 1))
                await elem.click()
                await asyncio.sleep(3)
                remaining = [await self._query(page, browser_type, item) for item in selectors]
                return not any(remaining)
            return True
        except Exception as exc:
            logger.error("CAPTCHA error: %s", exc)
            return False

    async def _query(self, page: Any, browser_type: str, selector: str) -> Any:
        if browser_type == "nodriver":
            return await page.select(selector)
        return await page.query_selector(selector)

    async def _read_page_state(self, page: Any, browser_type: str) -> tuple[str, str, str]:
        if browser_type == "nodriver":
            title = await page.evaluate("document.title")
            body_text = await page.evaluate("document.body ? document.body.innerText : ''")
            current_url = await page.evaluate("window.location.href")
            return str(title or ""), str(body_text or ""), str(current_url or "")
        title = await page.title()
        body_text = await page.locator("body").inner_text()
        return str(title or ""), str(body_text or ""), str(page.url or "")

    def _looks_like_cloudflare_challenge(self, lowered: str) -> bool:
        markers = [
            "just a moment",
            "performing security verification",
            "verify you are not a bot",
            "checking your browser",
            "cloudflare",
            "security service to protect against malicious bots",
            "turnstile",
        ]
        return any(marker in lowered for marker in markers)


class BlockingPageError(RuntimeError):
    """Raised when the remote page is not the actual chat UI."""


class AuthenticationRequiredError(RuntimeError):
    """Raised when the target site redirects the browser to an auth wall."""


class ConsensusScraper:
    def __init__(self, sessions: SessionManager):
        self.sessions = sessions
        self.captcha = CaptchaHandler()

    async def chat(self, request: ChatRequest):
        tab = None
        profile = BrowserChatProfile.from_request(request)

        try:
            tab = await self.sessions.get_session(
                request_session_id=request.session_id,
                headless=request.headless,
                profile=profile,
            )

            if request.solve_captcha:
                success = await self.captcha.check_and_solve(tab.page, tab.browser_type)
                if not success:
                    yield json.dumps({"error": "CAPTCHA failed", "retry": True, "session_id": tab.session_id})
                    return

            if tab.browser_type == "nodriver":
                async for chunk in self._nodriver_chat(tab, request, profile):
                    yield chunk
            else:
                async for chunk in self._playwright_chat(tab, request, profile):
                    yield chunk
        except Exception as exc:
            logger.error("Chat error: %s", exc)
            yield json.dumps({"error": str(exc), "session_id": request.session_id})
        finally:
            if tab:
                self.sessions.release(tab.session_id)

    async def _nodriver_chat(self, tab: BrowserTab, request: ChatRequest, profile: BrowserChatProfile):
        page = tab.page
        await page.sleep(1)
        await self._assert_not_blocked(page, tab.browser_type)
        previous_response = await self._extract_nodriver_response(page, profile.response_selectors)
        input_box = await self._find_nodriver_input(page, profile.input_selectors, profile.timeout_seconds)
        if not input_box:
            yield json.dumps({"error": "Chat input not found", "session_id": tab.session_id})
            return

        await input_box.send_keys(request.message)
        await page.sleep(0.4)
        submitted = await self._submit_nodriver(page, input_box, profile.submit_selectors)
        if not submitted:
            await input_box.send_keys("\n")

        last_text = ""
        stable_count = 0
        seen_new_response = False
        max_polls = max(1, int(profile.timeout_seconds / profile.poll_interval_seconds))

        for _ in range(max_polls):
            await page.sleep(profile.poll_interval_seconds)
            current_text = await self._extract_nodriver_response(page, profile.response_selectors)
            if not current_text:
                continue
            if not seen_new_response:
                if self._looks_like_same_turn(previous_response, current_text):
                    continue
                seen_new_response = True
            if current_text != last_text:
                delta = self._compute_delta(last_text, current_text)
                last_text = current_text
                stable_count = 0
                if delta:
                    yield json.dumps(
                        {
                            "type": "delta",
                            "content": delta,
                            "session_id": tab.session_id,
                        }
                    )
            else:
                stable_count += 1
                if stable_count >= profile.stable_polls:
                    break

        sources = await self._extract_nodriver_sources(page, profile.source_selectors)
        await self._assert_valid_completion(last_text, sources, page, tab.browser_type)
        tab.last_response_text = last_text
        yield json.dumps(
            {
                "type": "complete",
                "content": last_text,
                "sources": sources,
                "session_id": tab.session_id,
                "target_url": profile.target_url,
            }
        )

    async def _playwright_chat(self, tab: BrowserTab, request: ChatRequest, profile: BrowserChatProfile):
        page = tab.page
        await self._assert_not_blocked(page, tab.browser_type)
        previous_response = await self._extract_playwright_response(page, profile.response_selectors)
        input_selector = await self._find_playwright_selector(page, profile.input_selectors, profile.timeout_seconds)
        if not input_selector:
            yield json.dumps({"error": "Chat input not found", "session_id": tab.session_id})
            return

        locator = page.locator(input_selector).first
        await locator.click()
        tag_name = await locator.evaluate("(node) => node.tagName.toLowerCase()")
        if tag_name in {"textarea", "input"}:
            await locator.fill("")
            await locator.type(request.message, delay=35)
        else:
            await locator.fill("")
            await locator.type(request.message)
        submitted = await self._submit_playwright(page, locator, profile.submit_selectors)
        if not submitted:
            await locator.press("Enter")

        last_text = ""
        stable_count = 0
        seen_new_response = False
        max_polls = max(1, int(profile.timeout_seconds / profile.poll_interval_seconds))

        for _ in range(max_polls):
            await asyncio.sleep(profile.poll_interval_seconds)
            current_text = await self._extract_playwright_response(page, profile.response_selectors)
            if not current_text:
                continue
            if not seen_new_response:
                if self._looks_like_same_turn(previous_response, current_text):
                    continue
                seen_new_response = True
            if current_text != last_text:
                delta = self._compute_delta(last_text, current_text)
                last_text = current_text
                stable_count = 0
                if delta:
                    yield json.dumps(
                        {
                            "type": "delta",
                            "content": delta,
                            "session_id": tab.session_id,
                        }
                    )
            else:
                stable_count += 1
                if stable_count >= profile.stable_polls:
                    break

        sources = await self._extract_playwright_sources(page, profile.source_selectors)
        await self._assert_valid_completion(last_text, sources, page, tab.browser_type)
        tab.last_response_text = last_text
        yield json.dumps(
            {
                "type": "complete",
                "content": last_text,
                "sources": sources,
                "session_id": tab.session_id,
                "target_url": profile.target_url,
            }
        )

    async def _find_playwright_selector(self, page: Any, selectors: list[str], timeout_seconds: int) -> str | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for selector in selectors:
                try:
                    count = await page.locator(selector).count()
                    if count:
                        return selector
                except PlaywrightError:
                    continue
            await asyncio.sleep(0.25)
        return None

    async def _find_nodriver_input(self, page: Any, selectors: list[str], timeout_seconds: int) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for selector in selectors:
                elem = await page.select(selector)
                if elem:
                    return elem
            await page.sleep(0.25)
        return None

    async def _extract_playwright_response(self, page: Any, selectors: list[str]) -> str:
        for selector in selectors:
            try:
                elements = await page.query_selector_all(selector)
            except PlaywrightError:
                continue
            for element in reversed(elements[-8:]):
                try:
                    text = (await element.inner_text()).strip()
                except PlaywrightError:
                    continue
                if self._should_skip_response_text(text):
                    continue
                return text
        return ""

    async def _extract_nodriver_response(self, page: Any, selectors: list[str]) -> str:
        for selector in selectors:
            elements = await page.select_all(selector)
            for element in reversed(elements[-8:]):
                text = (await element.get_text()).strip()
                if self._should_skip_response_text(text):
                    continue
                return text
        return ""

    async def _extract_playwright_sources(self, page: Any, selectors: list[str]) -> list[dict[str, str]]:
        seen: set[str] = set()
        sources: list[dict[str, str]] = []
        for selector in selectors:
            try:
                links = await page.query_selector_all(selector)
            except PlaywrightError:
                continue
            for link in links[:10]:
                href = await link.get_attribute("href")
                if not href or href in seen:
                    continue
                text = (await link.inner_text()).strip() or href
                seen.add(href)
                sources.append({"title": text[:200], "url": href})
        return sources

    async def _extract_nodriver_sources(self, page: Any, selectors: list[str]) -> list[dict[str, str]]:
        seen: set[str] = set()
        sources: list[dict[str, str]] = []
        for selector in selectors:
            links = await page.select_all(selector)
            for link in links[:10]:
                href = await link.get_attribute("href")
                if not href or href in seen:
                    continue
                text = (await link.get_text()).strip() or href
                seen.add(href)
                sources.append({"title": text[:200], "url": href})
        return sources

    async def _submit_playwright(self, page: Any, input_locator: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                button = page.locator(selector).first
                if await button.count() == 0:
                    continue
                if await button.is_disabled():
                    continue
                await button.click()
                return True
            except PlaywrightError:
                continue
            except Exception:
                continue
        return False

    async def _submit_nodriver(self, page: Any, input_box: Any, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                button = await page.select(selector)
                if not button:
                    continue
                await button.click()
                return True
            except Exception:
                continue
        return False

    def _compute_delta(self, previous: str, current: str) -> str:
        if not previous:
            return current
        if current.startswith(previous):
            return current[len(previous) :]
        if previous in current:
            return current.split(previous, 1)[1]
        return current

    def _should_skip_response_text(self, text: str) -> bool:
        lowered = text.lower()
        if not lowered.strip():
            return True
        skip_markers = [
            "something went wrong",
            "a quick page refresh usually fixes this",
        ]
        if any(marker in lowered for marker in skip_markers):
            return True
        alphabetic_count = sum(char.isalpha() for char in text)
        if alphabetic_count < 24:
            return True
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        nonword_lines = sum(1 for line in lines if not any(char.isalpha() for char in line))
        if lines and nonword_lines / max(len(lines), 1) > 0.5:
            return True
        return False

    def _looks_like_same_turn(self, previous: str, current: str) -> bool:
        previous_clean = previous.strip()
        current_clean = current.strip()
        if not current_clean:
            return True
        if not previous_clean:
            return False
        if current_clean == previous_clean:
            return True
        if current_clean in previous_clean:
            return True
        overlap = min(len(previous_clean), len(current_clean), 400)
        if overlap and previous_clean[:overlap] == current_clean[:overlap]:
            return True
        return False

    async def _assert_not_blocked(self, page: Any, browser_type: str) -> None:
        title, body_text = await self._read_page_state(page, browser_type)
        current_url = await self._read_page_url(page, browser_type)
        if self._is_auth_wall(current_url, title, body_text):
            raise AuthenticationRequiredError(
                "Target page redirected to a sign-in/sign-up wall. "
                "Authenticate in the attached browser profile before using the API."
            )
        if self._is_blocked_text(title, body_text):
            raise BlockingPageError(
                "Target page is blocked by a security verification screen; "
                "the chat UI is not reachable in the current browser session."
            )

    async def _read_page_state(self, page: Any, browser_type: str) -> tuple[str, str]:
        if browser_type == "nodriver":
            title = await page.evaluate("document.title")
            body_text = await page.evaluate("document.body ? document.body.innerText : ''")
            return str(title or ""), str(body_text or "")
        title = await page.title()
        body_text = await page.locator("body").inner_text()
        return str(title or ""), str(body_text or "")

    async def _read_page_url(self, page: Any, browser_type: str) -> str:
        if browser_type == "nodriver":
            current_url = await page.evaluate("window.location.href")
            return str(current_url or "")
        return str(page.url or "")

    async def _assert_valid_completion(
        self,
        content: str,
        sources: list[dict[str, str]],
        page: Any,
        browser_type: str,
    ) -> None:
        if content.strip():
            return
        if any("cloudflare" in (source.get("title", "") + source.get("url", "")).lower() for source in sources):
            raise BlockingPageError(
                "Target page is blocked by a security verification screen; "
                "the chat UI is not reachable in the current browser session."
            )
        title, body_text = await self._read_page_state(page, browser_type)
        current_url = await self._read_page_url(page, browser_type)
        if self._is_auth_wall(current_url, title, body_text):
            raise AuthenticationRequiredError(
                "Target page redirected to a sign-in/sign-up wall. "
                "Authenticate in the attached browser profile before using the API."
            )
        if self._is_blocked_text(title, body_text):
            raise BlockingPageError(
                "Target page is blocked by a security verification screen; "
                "the chat UI is not reachable in the current browser session."
            )
        raise RuntimeError(
            "No assistant response was extracted from the page. "
            "The browser likely reached a landing page or the selectors do not match the live chat UI."
        )

    def _is_blocked_text(self, title: str, body_text: str) -> bool:
        lowered = f"{title}\n{body_text}".lower()
        block_markers = [
            "just a moment",
            "performing security verification",
            "verify you are not a bot",
            "checking your browser",
            "cloudflare",
            "security service to protect against malicious bots",
        ]
        return any(marker in lowered for marker in block_markers)

    def _is_auth_wall(self, current_url: str, title: str, body_text: str) -> bool:
        lowered = f"{current_url}\n{title}\n{body_text}".lower()
        auth_markers = [
            "/sign-up",
            "/sign-in",
            "create a free account to continue",
            "continue with google",
            "sign up - consensus",
            "sign in - consensus",
        ]
        return any(marker in lowered for marker in auth_markers)


sessions = SessionManager()
scraper = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scraper
    scraper = ConsensusScraper(sessions)
    console.print("[bold green]Browser chat API ready[/bold green]")
    yield
    await sessions.cleanup()
    console.print("[bold red]Shutdown complete[/bold red]")


app = FastAPI(
    title="Browser Chat Bridge API",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    async def event_stream():
        async for chunk in scraper.chat(request):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/chat/sync")
async def chat_sync(request: ChatRequest):
    request.stream = False
    fallback_chunks: list[str] = []
    last_payload: dict[str, Any] | None = None

    async for chunk in scraper.chat(request):
        payload = json.loads(chunk)
        last_payload = payload
        if payload.get("error"):
            raise HTTPException(502, payload["error"])
        if payload.get("type") == "complete":
            payload.setdefault("session_id", request.session_id)
            payload.setdefault("sources", [])
            payload.setdefault("target_url", request.target_url or DEFAULT_TARGET_URL)
            return payload
        if payload.get("content"):
            fallback_chunks.append(payload["content"])

    return {
        "type": "complete",
        "content": "".join(fallback_chunks),
        "sources": [],
        "session_id": request.session_id or (last_payload or {}).get("session_id"),
        "target_url": request.target_url or DEFAULT_TARGET_URL,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "sessions": len(sessions.sessions),
        "nodriver": NODRIVER_AVAILABLE,
        "playwright": PLAYWRIGHT_AVAILABLE,
        "default_target_url": DEFAULT_TARGET_URL,
        "browser_driver": DEFAULT_BROWSER_DRIVER,
        "browser_mode": DEFAULT_BROWSER_MODE,
        "browser_profile_dir": DEFAULT_BROWSER_PROFILE_DIR,
        "browser_cdp_url": DEFAULT_BROWSER_CDP_URL,
    }


@app.get("/")
async def root():
    return HTMLResponse(
        """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Browser Chat Bridge</title>
        <style>
            :root {
                --bg: #f3efe5;
                --panel: #fffdf8;
                --panel-strong: #f7f1e4;
                --border: #d9ccb5;
                --text: #1f2933;
                --muted: #5f6c7b;
                --accent: #234e52;
                --accent-soft: #d8ebe7;
                --user: #173f5f;
                --user-soft: #dbe7f2;
                --error: #7f1d1d;
                --error-soft: #fee2e2;
            }
            * { box-sizing: border-box; }
            body {
                margin: 0;
                min-height: 100vh;
                font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                color: var(--text);
                background:
                    radial-gradient(circle at top left, rgba(35, 78, 82, 0.14), transparent 22rem),
                    radial-gradient(circle at bottom right, rgba(23, 63, 95, 0.12), transparent 28rem),
                    var(--bg);
            }
            .app {
                max-width: 1100px;
                margin: 0 auto;
                padding: 24px;
            }
            .shell {
                display: grid;
                grid-template-columns: 320px 1fr;
                gap: 18px;
                align-items: start;
            }
            .card {
                background: rgba(255, 253, 248, 0.9);
                border: 1px solid var(--border);
                border-radius: 20px;
                box-shadow: 0 18px 60px rgba(31, 41, 51, 0.08);
                backdrop-filter: blur(12px);
            }
            .sidebar {
                padding: 20px;
                position: sticky;
                top: 24px;
            }
            h1 {
                margin: 0 0 10px;
                font-size: 28px;
                line-height: 1.05;
            }
            .hint {
                margin: 0 0 18px;
                color: var(--muted);
                font-size: 14px;
                line-height: 1.5;
            }
            .field {
                margin-bottom: 14px;
            }
            .field label {
                display: block;
                margin-bottom: 6px;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: var(--muted);
            }
            input, textarea {
                width: 100%;
                border: 1px solid var(--border);
                background: var(--panel);
                color: var(--text);
                border-radius: 14px;
                padding: 12px 14px;
                outline: none;
                font: inherit;
            }
            input:focus, textarea:focus {
                border-color: var(--accent);
                box-shadow: 0 0 0 3px rgba(35, 78, 82, 0.12);
            }
            textarea {
                min-height: 112px;
                resize: vertical;
            }
            .toolbar {
                display: flex;
                gap: 10px;
                margin-top: 16px;
            }
            button {
                border: none;
                border-radius: 999px;
                padding: 11px 16px;
                font: inherit;
                cursor: pointer;
                transition: transform 0.12s ease, opacity 0.12s ease, background 0.12s ease;
            }
            button:hover { transform: translateY(-1px); }
            button:disabled { opacity: 0.55; cursor: wait; transform: none; }
            .primary {
                background: var(--accent);
                color: white;
            }
            .secondary {
                background: var(--panel-strong);
                color: var(--text);
                border: 1px solid var(--border);
            }
            .status {
                display: grid;
                gap: 10px;
                margin-top: 18px;
                padding: 14px;
                border-radius: 16px;
                background: var(--panel-strong);
            }
            .status-row {
                display: flex;
                justify-content: space-between;
                gap: 12px;
                font-size: 13px;
            }
            .status-row code {
                font-size: 12px;
                color: var(--accent);
                word-break: break-all;
            }
            .chat {
                min-height: calc(100vh - 48px);
                display: grid;
                grid-template-rows: auto 1fr auto;
            }
            .chat-header {
                padding: 18px 20px;
                border-bottom: 1px solid var(--border);
                display: flex;
                justify-content: space-between;
                gap: 16px;
                align-items: center;
            }
            .chat-header h2 {
                margin: 0;
                font-size: 18px;
            }
            .chat-header p {
                margin: 4px 0 0;
                color: var(--muted);
                font-size: 13px;
            }
            .pill {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 8px 12px;
                border-radius: 999px;
                background: var(--accent-soft);
                color: var(--accent);
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.04em;
                text-transform: uppercase;
            }
            .messages {
                padding: 20px;
                display: flex;
                flex-direction: column;
                gap: 14px;
                overflow: auto;
            }
            .empty {
                min-height: 320px;
                display: grid;
                place-items: center;
                text-align: center;
                color: var(--muted);
                padding: 28px;
            }
            .message {
                display: grid;
                gap: 8px;
                max-width: 88%;
            }
            .message.user {
                align-self: end;
            }
            .message.assistant, .message.system {
                align-self: start;
            }
            .bubble {
                padding: 14px 16px;
                border-radius: 18px;
                white-space: pre-wrap;
                line-height: 1.5;
                border: 1px solid transparent;
            }
            .user .bubble {
                background: var(--user-soft);
                color: var(--user);
                border-bottom-right-radius: 6px;
            }
            .assistant .bubble {
                background: var(--panel-strong);
                border-color: var(--border);
                border-bottom-left-radius: 6px;
            }
            .system .bubble {
                background: var(--error-soft);
                color: var(--error);
                border-color: rgba(127, 29, 29, 0.15);
            }
            .meta {
                font-size: 12px;
                color: var(--muted);
                padding: 0 4px;
            }
            .sources {
                display: grid;
                gap: 8px;
                padding-left: 4px;
            }
            .source-link {
                color: var(--accent);
                text-decoration: none;
                font-size: 13px;
            }
            .source-link:hover {
                text-decoration: underline;
            }
            .composer {
                border-top: 1px solid var(--border);
                padding: 18px 20px 20px;
                display: grid;
                gap: 12px;
            }
            .composer-row {
                display: flex;
                gap: 10px;
                align-items: flex-end;
            }
            .composer-row textarea {
                min-height: 76px;
                margin: 0;
            }
            .composer-row button {
                min-width: 124px;
                height: 48px;
            }
            .composer-note {
                font-size: 12px;
                color: var(--muted);
            }
            @media (max-width: 960px) {
                .shell {
                    grid-template-columns: 1fr;
                }
                .sidebar {
                    position: static;
                }
                .chat {
                    min-height: auto;
                }
                .message {
                    max-width: 100%;
                }
                .composer-row {
                    flex-direction: column;
                    align-items: stretch;
                }
                .composer-row button {
                    width: 100%;
                }
            }
        </style>
    </head>
    <body>
        <main class="app">
            <div class="shell">
                <aside class="card sidebar">
                    <h1>Browser Chat Bridge</h1>
                    <p class="hint">The localhost UI now keeps one remote browser tab per chat session. New chat creates a fresh remote tab. Follow-up questions stay on the same remote tab by reusing the returned session id.</p>

                    <div class="field">
                        <label for="targetUrl">Target URL</label>
                        <input id="targetUrl" value="https://consensus.app/search/" />
                    </div>

                    <div class="field">
                        <label for="message">Draft</label>
                        <textarea id="message" placeholder="Ask a question...">What are the effects of caffeine on sleep?</textarea>
                    </div>

                    <div class="toolbar">
                        <button id="sendButton" class="primary" type="button">Send</button>
                        <button id="newChatButton" class="secondary" type="button">New Chat</button>
                    </div>

                    <div class="status">
                        <div class="status-row">
                            <span>Session</span>
                            <code id="sessionLabel">new</code>
                        </div>
                        <div class="status-row">
                            <span>Remote target</span>
                            <code id="targetLabel">https://consensus.app/search/</code>
                        </div>
                        <div class="status-row">
                            <span>Status</span>
                            <code id="statusLabel">idle</code>
                        </div>
                    </div>
                </aside>

                <section class="card chat">
                    <header class="chat-header">
                        <div>
                            <h2>Localhost Chat UI</h2>
                            <p>Messages on the same session reuse the same remote browser tab.</p>
                        </div>
                        <div class="pill" id="modeLabel">New Chat</div>
                    </header>

                    <div class="messages" id="messages">
                        <div class="empty" id="emptyState">
                            <div>
                                <strong>Ready</strong>
                                <p>Send the first message to open a new remote browser tab. Follow-ups will stay inside that same session.</p>
                            </div>
                        </div>
                    </div>

                    <footer class="composer">
                        <div class="composer-row">
                            <textarea id="composer" placeholder="Ask a question or send a follow-up..."></textarea>
                            <button id="composerSendButton" class="primary" type="button">Send Message</button>
                        </div>
                        <div class="composer-note">Press Shift+Enter for a newline. Press Enter to send.</div>
                    </footer>
                </section>
            </div>
        </main>

        <script>
            const state = {
                sessionId: null,
                sending: false,
                messages: []
            };

            const elements = {
                targetUrl: document.getElementById('targetUrl'),
                targetLabel: document.getElementById('targetLabel'),
                sessionLabel: document.getElementById('sessionLabel'),
                statusLabel: document.getElementById('statusLabel'),
                modeLabel: document.getElementById('modeLabel'),
                messages: document.getElementById('messages'),
                emptyState: document.getElementById('emptyState'),
                draft: document.getElementById('message'),
                composer: document.getElementById('composer'),
                sendButton: document.getElementById('sendButton'),
                composerSendButton: document.getElementById('composerSendButton'),
                newChatButton: document.getElementById('newChatButton')
            };

            function setStatus(text) {
                elements.statusLabel.textContent = text;
            }

            function syncSessionUI() {
                elements.sessionLabel.textContent = state.sessionId || 'new';
                elements.modeLabel.textContent = state.sessionId ? 'Follow-up Mode' : 'New Chat';
                elements.targetLabel.textContent = elements.targetUrl.value || '';
                const disabled = state.sending;
                elements.sendButton.disabled = disabled;
                elements.composerSendButton.disabled = disabled;
            }

            function createMessage(role, content, options = {}) {
                return {
                    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                    role,
                    content,
                    sources: options.sources || [],
                    meta: options.meta || ''
                };
            }

            function renderMessages() {
                if (state.messages.length === 0) {
                    elements.emptyState.style.display = 'grid';
                    return;
                }

                elements.emptyState.style.display = 'none';
                const nodes = state.messages.map((message) => {
                    const wrapper = document.createElement('article');
                    wrapper.className = `message ${message.role}`;

                    const meta = document.createElement('div');
                    meta.className = 'meta';
                    meta.textContent = message.meta || (message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Remote browser' : 'System');
                    wrapper.appendChild(meta);

                    const bubble = document.createElement('div');
                    bubble.className = 'bubble';
                    bubble.textContent = message.content;
                    wrapper.appendChild(bubble);

                    if (message.sources && message.sources.length) {
                        const sources = document.createElement('div');
                        sources.className = 'sources';
                        message.sources.forEach((source, index) => {
                            const link = document.createElement('a');
                            link.className = 'source-link';
                            link.href = source.url;
                            link.target = '_blank';
                            link.rel = 'noreferrer';
                            link.textContent = source.title || `Source ${index + 1}`;
                            sources.appendChild(link);
                        });
                        wrapper.appendChild(sources);
                    }

                    return wrapper;
                });

                elements.messages.innerHTML = '';
                nodes.forEach((node) => elements.messages.appendChild(node));
                elements.messages.scrollTop = elements.messages.scrollHeight;
            }

            function addMessage(role, content, options) {
                const message = createMessage(role, content, options);
                state.messages.push(message);
                renderMessages();
                return message;
            }

            function resetChat() {
                state.sessionId = null;
                state.messages = [];
                renderMessages();
                setStatus('idle');
                syncSessionUI();
            }

            async function sendMessage(rawMessage) {
                const message = rawMessage.trim();
                if (!message || state.sending) return;

                state.sending = true;
                syncSessionUI();
                setStatus(state.sessionId ? 'sending follow-up' : 'opening new remote chat');

                addMessage('user', message, { meta: state.sessionId ? `Follow-up on ${state.sessionId}` : 'New session' });
                const assistantMessage = addMessage('assistant', '', { meta: 'Streaming from remote browser' });

                const payload = {
                    message,
                    target_url: elements.targetUrl.value,
                    stream: true
                };
                if (state.sessionId) {
                    payload.session_id = state.sessionId;
                }

                try {
                    const response = await fetch('/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });

                    if (!response.ok || !response.body) {
                        throw new Error(`Request failed with status ${response.status}`);
                    }

                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';

                    while (true) {
                        const { value, done } = await reader.read();
                        if (done) break;

                        buffer += decoder.decode(value, { stream: true });
                        const chunks = buffer.split('\\n\\n');
                        buffer = chunks.pop() || '';

                        for (const chunk of chunks) {
                            const lines = chunk.split('\\n');
                            for (const line of lines) {
                                if (!line.startsWith('data: ')) continue;
                                const data = line.slice(6);
                                if (data === '[DONE]') continue;
                                const parsed = JSON.parse(data);

                                if (parsed.error) {
                                    assistantMessage.role = 'system';
                                    assistantMessage.meta = 'Remote error';
                                    assistantMessage.content = parsed.error;
                                    renderMessages();
                                    setStatus('error');
                                    continue;
                                }

                                if (parsed.type === 'delta') {
                                    assistantMessage.content += parsed.content || '';
                                    if (parsed.session_id && !state.sessionId) {
                                        state.sessionId = parsed.session_id;
                                    }
                                    renderMessages();
                                    setStatus('streaming');
                                }

                                if (parsed.type === 'complete') {
                                    assistantMessage.content = parsed.content || assistantMessage.content;
                                    assistantMessage.sources = parsed.sources || [];
                                    assistantMessage.meta = parsed.session_id ? `Remote session ${parsed.session_id}` : 'Remote browser';
                                    if (parsed.session_id) {
                                        state.sessionId = parsed.session_id;
                                    }
                                    renderMessages();
                                    setStatus('ready');
                                }
                            }
                        }
                    }
                } catch (error) {
                    assistantMessage.role = 'system';
                    assistantMessage.meta = 'Client error';
                    assistantMessage.content = error.message || String(error);
                    renderMessages();
                    setStatus('error');
                } finally {
                    state.sending = false;
                    elements.targetLabel.textContent = elements.targetUrl.value || '';
                    syncSessionUI();
                }
            }

            function handleSendFrom(element) {
                const value = element.value;
                if (!value.trim()) return;
                sendMessage(value);
                element.value = '';
            }

            elements.sendButton.addEventListener('click', () => handleSendFrom(elements.draft));
            elements.composerSendButton.addEventListener('click', () => handleSendFrom(elements.composer));
            elements.newChatButton.addEventListener('click', resetChat);
            elements.targetUrl.addEventListener('input', syncSessionUI);
            elements.composer.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    handleSendFrom(elements.composer);
                }
            });
            elements.draft.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    handleSendFrom(elements.draft);
                }
            });

            syncSessionUI();
        </script>
    </body>
    </html>
    """
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002, reload=True)
