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
from urllib.parse import urlparse

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


@dataclass(frozen=True)
class BrowserTargetAdapter:
    name: str
    default_target_url: str
    default_input_selectors: tuple[str, ...]
    default_response_selectors: tuple[str, ...]
    default_source_selectors: tuple[str, ...]
    default_submit_selectors: tuple[str, ...]
    blocked_markers: tuple[str, ...] = ()
    auth_markers: tuple[str, ...] = ()
    skip_response_markers: tuple[str, ...] = ()
    minimum_response_alpha_chars: int = 24
    latest_response_window: int = 8

    def matches(self, target_url: str) -> bool:
        return False

    def build_profile(self, request: "ChatRequest", target_url: str) -> "BrowserChatProfile":
        return BrowserChatProfile(
            target_url=target_url,
            adapter=self,
            input_selectors=request.input_selectors or _csv_env(
                "BROWSER_CHAT_INPUT_SELECTORS",
                list(self.default_input_selectors),
            ),
            response_selectors=request.response_selectors or _csv_env(
                "BROWSER_CHAT_RESPONSE_SELECTORS",
                list(self.default_response_selectors),
            ),
            source_selectors=request.source_selectors or _csv_env(
                "BROWSER_CHAT_SOURCE_SELECTORS",
                list(self.default_source_selectors),
            ),
            submit_selectors=request.submit_selectors or _csv_env(
                "BROWSER_CHAT_SUBMIT_SELECTORS",
                list(self.default_submit_selectors),
            ),
        )

    def should_skip_response_text(self, text: str) -> bool:
        lowered = text.lower()
        if not lowered.strip():
            return True
        if any(marker in lowered for marker in self.skip_response_markers):
            return True
        alphabetic_count = sum(char.isalpha() for char in text)
        if alphabetic_count < self.minimum_response_alpha_chars:
            return True
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        nonword_lines = sum(1 for line in lines if not any(char.isalpha() for char in line))
        if lines and nonword_lines / max(len(lines), 1) > 0.5:
            return True
        return False

    def looks_like_same_turn(self, previous: str, current: str) -> bool:
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

    def is_blocked(self, current_url: str, title: str, body_text: str) -> bool:
        lowered = f"{current_url}\n{title}\n{body_text}".lower()
        return any(marker in lowered for marker in self.blocked_markers)

    def is_auth_wall(self, current_url: str, title: str, body_text: str) -> bool:
        lowered = f"{current_url}\n{title}\n{body_text}".lower()
        return any(marker in lowered for marker in self.auth_markers)


class GenericTargetAdapter(BrowserTargetAdapter):
    def matches(self, target_url: str) -> bool:
        return True


class ConsensusTargetAdapter(BrowserTargetAdapter):
    def matches(self, target_url: str) -> bool:
        hostname = urlparse(target_url).netloc.lower()
        return hostname.endswith("consensus.app")


class ZAITargetAdapter(BrowserTargetAdapter):
    def matches(self, target_url: str) -> bool:
        hostname = urlparse(target_url).netloc.lower()
        return hostname in {"chat.z.ai", "z.ai"}


CONSENSUS_TARGET_ADAPTER = ConsensusTargetAdapter(
    name="consensus",
    default_target_url="https://consensus.app/search/",
    default_input_selectors=(
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="Message"]',
        "textarea",
        'input[placeholder*="Ask"]',
        'input[placeholder*="Message"]',
        'div[contenteditable="true"]',
    ),
    default_response_selectors=(
        "main .prose",
        ".prose",
        '[data-testid="search-results-list"]',
        '[data-testid="search-result"]',
        '[data-testid="drawer-content"]',
        '[data-message-author-role="assistant"]',
        '[data-testid*="assistant"]',
        "main article",
        '[class*="response"]',
        '[class*="answer"]',
        "article",
    ),
    default_source_selectors=(
        '[data-testid="search-result"] a[href^="http"]',
        '[data-testid="drawer-content"] a[href^="http"]',
        'a[href*="doi.org"]',
        'a[href*="pubmed"]',
        'a[href*="ncbi.nlm.nih.gov"]',
        'a[href^="http"]',
    ),
    default_submit_selectors=(
        "#search-button",
        '[data-testid="search-button"]',
        'button[type="submit"]',
        'button[aria-label*="Submit"]',
        'button[aria-label*="Search"]',
    ),
    blocked_markers=(
        "just a moment",
        "performing security verification",
        "verify you are not a bot",
        "checking your browser",
        "cloudflare",
        "security service to protect against malicious bots",
    ),
    auth_markers=(
        "/sign-up",
        "/sign-in",
        "create a free account to continue",
        "continue with google",
        "sign up - consensus",
        "sign in - consensus",
    ),
    skip_response_markers=(
        "something went wrong",
        "a quick page refresh usually fixes this",
    ),
)

ZAI_TARGET_ADAPTER = ZAITargetAdapter(
    name="zai",
    default_target_url="https://chat.z.ai/",
    default_input_selectors=(
        'textarea[placeholder*="How can I help"]',
        "textarea",
    ),
    default_response_selectors=(
        '#response-content-container > .markdown-prose > :not(.thinking-chain-container):not(.overflow-hidden)',
        '.chat-assistant #response-content-container > .markdown-prose > :not(.thinking-chain-container):not(.overflow-hidden)',
        '.chat-assistant .markdown-prose > ul',
        '.chat-assistant .markdown-prose > ol',
        '.chat-assistant .markdown-prose > p',
        '.chat-assistant .markdown-prose',
    ),
    default_source_selectors=(
        '#response-content-container a[href^="http"]',
        '.chat-assistant a[href^="http"]',
    ),
    default_submit_selectors=(
        "#send-message-button",
        'button.sendMessageButton',
        'button[type="submit"]',
    ),
    auth_markers=(
        "/auth",
        "/login",
        "continue with google",
        "continue with github",
        "create account",
    ),
    skip_response_markers=(
        "thinking...",
        "thought process",
        "\nskip\n",
    ),
    minimum_response_alpha_chars=1,
)

GENERIC_TARGET_ADAPTER = GenericTargetAdapter(
    name="generic",
    default_target_url="about:blank",
    default_input_selectors=(
        'textarea[placeholder*="Ask"]',
        'textarea[placeholder*="Message"]',
        "textarea",
        'input[placeholder*="Ask"]',
        'input[placeholder*="Message"]',
        'div[contenteditable="true"]',
    ),
    default_response_selectors=(
        '[data-message-author-role="assistant"]',
        '[data-testid*="assistant"]',
        "main .prose",
        ".prose",
        "main article",
        '[class*="response"]',
        '[class*="answer"]',
        "article",
    ),
    default_source_selectors=('a[href^="http"]',),
    default_submit_selectors=(
        'button[type="submit"]',
        'button[aria-label*="Submit"]',
        'button[aria-label*="Send"]',
        'button[aria-label*="Search"]',
    ),
)

TARGET_ADAPTERS: tuple[BrowserTargetAdapter, ...] = (
    CONSENSUS_TARGET_ADAPTER,
    ZAI_TARGET_ADAPTER,
    GENERIC_TARGET_ADAPTER,
)


def resolve_target_adapter(target_url: str) -> BrowserTargetAdapter:
    for adapter in TARGET_ADAPTERS:
        if adapter.matches(target_url):
            return adapter
    return GENERIC_TARGET_ADAPTER


DEFAULT_TARGET_URL = os.getenv("BROWSER_CHAT_URL", CONSENSUS_TARGET_ADAPTER.default_target_url)
DEFAULT_INPUT_SELECTORS = _csv_env(
    "BROWSER_CHAT_INPUT_SELECTORS",
    list(CONSENSUS_TARGET_ADAPTER.default_input_selectors),
)
DEFAULT_RESPONSE_SELECTORS = _csv_env(
    "BROWSER_CHAT_RESPONSE_SELECTORS",
    list(CONSENSUS_TARGET_ADAPTER.default_response_selectors),
)
DEFAULT_SOURCE_SELECTORS = _csv_env(
    "BROWSER_CHAT_SOURCE_SELECTORS",
    list(CONSENSUS_TARGET_ADAPTER.default_source_selectors),
)
DEFAULT_SUBMIT_SELECTORS = _csv_env(
    "BROWSER_CHAT_SUBMIT_SELECTORS",
    list(CONSENSUS_TARGET_ADAPTER.default_submit_selectors),
)

DEFAULT_WAIT_TIMEOUT_SECONDS = int(os.getenv("BROWSER_CHAT_WAIT_TIMEOUT_SECONDS", "90"))
DEFAULT_POLL_INTERVAL_SECONDS = float(os.getenv("BROWSER_CHAT_POLL_INTERVAL_SECONDS", "1.0"))
DEFAULT_STABLE_POLLS = int(os.getenv("BROWSER_CHAT_STABLE_POLLS", "3"))
DEFAULT_BROWSER_DRIVER = os.getenv("BROWSER_CHAT_DRIVER", "playwright").strip().lower()
DEFAULT_BROWSER_MODE = os.getenv("BROWSER_CHAT_MODE", "persistent").strip().lower()
DEFAULT_BROWSER_WINDOW_MODE = os.getenv("BROWSER_CHAT_WINDOW_MODE", "background").strip().lower()
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
    session_link: str | None = Field(
        default=None,
        description="Direct URL for an existing remote chat that should be reopened or continued",
    )
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


class SessionCreateRequest(BaseModel):
    session_id: str | None = None
    session_link: str | None = None
    target_url: str | None = None
    headless: bool = False
    input_selectors: list[str] | None = None
    response_selectors: list[str] | None = None
    source_selectors: list[str] | None = None
    submit_selectors: list[str] | None = None

    def to_chat_request(self, message: str = "") -> ChatRequest:
        return ChatRequest(
            message=message or " ",
            session_id=self.session_id,
            session_link=self.session_link,
            stream=False,
            headless=self.headless,
            solve_captcha=True,
            target_url=self.target_url,
            input_selectors=self.input_selectors,
            response_selectors=self.response_selectors,
            source_selectors=self.source_selectors,
            submit_selectors=self.submit_selectors,
        )


class OpenAIChatCompletionRequest(BaseModel):
    model: str = "browser-chat"
    messages: list[dict[str, Any]]
    stream: bool = False
    session_id: str | None = None
    session_link: str | None = None
    target_url: str | None = None
    headless: bool = False
    input_selectors: list[str] | None = None
    response_selectors: list[str] | None = None
    source_selectors: list[str] | None = None
    submit_selectors: list[str] | None = None


class AnthropicMessagesRequest(BaseModel):
    model: str = "browser-chat"
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    session_id: str | None = None
    session_link: str | None = None
    target_url: str | None = None
    headless: bool = False
    input_selectors: list[str] | None = None
    response_selectors: list[str] | None = None
    source_selectors: list[str] | None = None
    submit_selectors: list[str] | None = None


@dataclass
class BrowserChatProfile:
    target_url: str
    adapter: BrowserTargetAdapter = field(repr=False)
    input_selectors: list[str]
    response_selectors: list[str]
    source_selectors: list[str]
    submit_selectors: list[str]
    timeout_seconds: int = DEFAULT_WAIT_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    stable_polls: int = DEFAULT_STABLE_POLLS

    @classmethod
    def from_request(cls, request: ChatRequest) -> "BrowserChatProfile":
        target_url = request.session_link or request.target_url or DEFAULT_TARGET_URL
        adapter = resolve_target_adapter(target_url)
        return adapter.build_profile(request, target_url)


@dataclass
class BrowserTab:
    session_id: str
    page: Any
    browser_type: str
    browser: Any = None
    browser_context: Any = None
    target_url: str | None = None
    current_url: str | None = None
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
            adapter=resolve_target_adapter(DEFAULT_TARGET_URL),
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
                current_url=profile.target_url,
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
            existing_page = self._find_existing_attached_page(context, target_url)
            if existing_page:
                page = existing_page
                await page.bring_to_front()
            else:
                page = await context.new_page()
                await page.goto(target_url, wait_until="domcontentloaded")
            return BrowserTab(
                session_id=sid,
                page=page,
                browser=self._playwright_browser,
                browser_context=context,
                browser_type="playwright",
                target_url=target_url,
                current_url=target_url,
                in_use=True,
                owns_page=existing_page is None,
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
                current_url=target_url,
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
            current_url=target_url,
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

    def _find_existing_attached_page(self, context: Any, target_url: str) -> Any | None:
        normalized_target = target_url.rstrip("/")
        for page in reversed(context.pages):
            page_url = str(getattr(page, "url", "") or "").rstrip("/")
            if not page_url:
                continue
            if page_url == normalized_target:
                return page
        return None

    def _playwright_launch_args(self) -> list[str]:
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size=1920,1080",
        ]
        if DEFAULT_BROWSER_WINDOW_MODE == "offscreen":
            args.extend(["--window-position=-2400,0"])
        elif DEFAULT_BROWSER_WINDOW_MODE in {"background", "minimized"}:
            args.extend(["--start-minimized"])
        return args

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
        tab.current_url = target_url

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


class BrowserChatScraper:
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
        await self._assert_not_blocked(page, tab.browser_type, profile)
        if request.session_link and not request.session_id:
            await self._wait_for_resume_ready(page, tab.browser_type, profile)
        previous_response = await self._snapshot_existing_response(
            page,
            tab.browser_type,
            profile,
            require_nonempty=bool(request.session_link and not request.session_id),
        )
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
            current_text = await self._extract_nodriver_response(page, profile)
            if not current_text:
                continue
            if not seen_new_response:
                if profile.adapter.looks_like_same_turn(previous_response, current_text):
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
        await self._assert_valid_completion(last_text, sources, page, tab.browser_type, profile)
        current_url = await self._read_page_url(page, tab.browser_type)
        tab.last_response_text = last_text
        tab.current_url = current_url
        yield json.dumps(
            {
                "type": "complete",
                "content": last_text,
                "sources": sources,
                "session_id": tab.session_id,
                "target_url": profile.target_url,
                "session_link": current_url,
            }
        )

    async def _playwright_chat(self, tab: BrowserTab, request: ChatRequest, profile: BrowserChatProfile):
        page = tab.page
        await self._assert_not_blocked(page, tab.browser_type, profile)
        if request.session_link and not request.session_id:
            await self._wait_for_resume_ready(page, tab.browser_type, profile)
        previous_response = await self._snapshot_existing_response(
            page,
            tab.browser_type,
            profile,
            require_nonempty=bool(request.session_link and not request.session_id),
        )
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
            current_text = await self._extract_playwright_response(page, profile)
            if not current_text:
                continue
            if not seen_new_response:
                if profile.adapter.looks_like_same_turn(previous_response, current_text):
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
        await self._assert_valid_completion(last_text, sources, page, tab.browser_type, profile)
        current_url = await self._read_page_url(page, tab.browser_type)
        tab.last_response_text = last_text
        tab.current_url = current_url
        yield json.dumps(
            {
                "type": "complete",
                "content": last_text,
                "sources": sources,
                "session_id": tab.session_id,
                "target_url": profile.target_url,
                "session_link": current_url,
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

    async def _extract_playwright_response(self, page: Any, profile: BrowserChatProfile) -> str:
        for selector in profile.response_selectors:
            try:
                elements = await page.query_selector_all(selector)
            except PlaywrightError:
                continue
            for element in reversed(elements[-profile.adapter.latest_response_window :]):
                try:
                    text = (await element.inner_text()).strip()
                except PlaywrightError:
                    continue
                if self._should_skip_response_text(text, profile.adapter):
                    continue
                return text
        return ""

    async def _extract_nodriver_response(self, page: Any, profile: BrowserChatProfile) -> str:
        for selector in profile.response_selectors:
            elements = await page.select_all(selector)
            for element in reversed(elements[-profile.adapter.latest_response_window :]):
                text = (await element.get_text()).strip()
                if self._should_skip_response_text(text, profile.adapter):
                    continue
                return text
        return ""

    async def _snapshot_existing_response(
        self,
        page: Any,
        browser_type: str,
        profile: BrowserChatProfile,
        require_nonempty: bool = False,
    ) -> str:
        last_text = ""
        stable_polls = 0
        max_polls = 16 if require_nonempty else 6
        saw_nonempty = False

        for _ in range(max_polls):
            if browser_type == "playwright":
                current_text = await self._extract_playwright_response(page, profile)
                await asyncio.sleep(0.35)
            else:
                current_text = await self._extract_nodriver_response(page, profile)
                await page.sleep(0.35)

            if current_text.strip():
                saw_nonempty = True

            if current_text == last_text:
                stable_polls += 1
                if stable_polls >= 2 and (saw_nonempty or not require_nonempty):
                    return current_text
                continue

            last_text = current_text
            stable_polls = 0

        return last_text

    async def _wait_for_resume_ready(self, page: Any, browser_type: str, profile: BrowserChatProfile) -> None:
        stable_polls = 0
        max_polls = 20

        for _ in range(max_polls):
            title, body_text = await self._read_page_state(page, browser_type)
            lowered = f"{title}\n{body_text}".lower()
            current_text = ""
            if browser_type == "playwright":
                current_text = await self._extract_playwright_response(page, profile)
                await asyncio.sleep(0.4)
            else:
                current_text = await self._extract_nodriver_response(page, profile)
                await page.sleep(0.4)

            if "loading..." not in lowered and current_text.strip():
                stable_polls += 1
                if stable_polls >= 2:
                    if browser_type == "playwright":
                        await asyncio.sleep(1.5)
                    else:
                        await page.sleep(1.5)
                    return
            else:
                stable_polls = 0

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

    def _should_skip_response_text(self, text: str, adapter: BrowserTargetAdapter) -> bool:
        return adapter.should_skip_response_text(text)

    async def _assert_not_blocked(self, page: Any, browser_type: str, profile: BrowserChatProfile) -> None:
        title, body_text = await self._read_page_state(page, browser_type)
        current_url = await self._read_page_url(page, browser_type)
        if profile.adapter.is_auth_wall(current_url, title, body_text):
            raise AuthenticationRequiredError(
                "Target page redirected to a sign-in/sign-up wall. "
                "Authenticate in the attached browser profile before using the API."
            )
        if profile.adapter.is_blocked(current_url, title, body_text):
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
        profile: BrowserChatProfile,
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
        if profile.adapter.is_auth_wall(current_url, title, body_text):
            raise AuthenticationRequiredError(
                "Target page redirected to a sign-in/sign-up wall. "
                "Authenticate in the attached browser profile before using the API."
            )
        if profile.adapter.is_blocked(current_url, title, body_text):
            raise BlockingPageError(
                "Target page is blocked by a security verification screen; "
                "the chat UI is not reachable in the current browser session."
            )
        raise RuntimeError(
            "No assistant response was extracted from the page. "
            "The browser likely reached a landing page or the selectors do not match the live chat UI."
        )


sessions = SessionManager()
scraper = None
ConsensusScraper = BrowserChatScraper


def _extract_text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict) and content.get("text"):
        return str(content["text"])
    return ""


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = _extract_text_from_message_content(message.get("content"))
        if text.strip():
            return text.strip()
    raise HTTPException(400, "At least one user message is required")


def _chat_request_from_openai(request: OpenAIChatCompletionRequest) -> ChatRequest:
    return ChatRequest(
        message=_last_user_message(request.messages),
        session_id=request.session_id,
        session_link=request.session_link,
        stream=request.stream,
        headless=request.headless,
        target_url=request.target_url,
        input_selectors=request.input_selectors,
        response_selectors=request.response_selectors,
        source_selectors=request.source_selectors,
        submit_selectors=request.submit_selectors,
    )


def _chat_request_from_anthropic(request: AnthropicMessagesRequest) -> ChatRequest:
    return ChatRequest(
        message=_last_user_message(request.messages),
        session_id=request.session_id,
        session_link=request.session_link,
        stream=request.stream,
        headless=request.headless,
        target_url=request.target_url,
        input_selectors=request.input_selectors,
        response_selectors=request.response_selectors,
        source_selectors=request.source_selectors,
        submit_selectors=request.submit_selectors,
    )


async def _collect_chat_result(request: ChatRequest) -> dict[str, Any]:
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
            payload.setdefault("target_url", request.session_link or request.target_url or DEFAULT_TARGET_URL)
            payload.setdefault("session_link", request.session_link or payload.get("target_url"))
            return payload
        if payload.get("content"):
            fallback_chunks.append(payload["content"])

    return {
        "type": "complete",
        "content": "".join(fallback_chunks),
        "sources": [],
        "session_id": request.session_id or (last_payload or {}).get("session_id"),
        "target_url": request.session_link or request.target_url or DEFAULT_TARGET_URL,
        "session_link": request.session_link or (last_payload or {}).get("session_link"),
    }


async def _session_info(tab: BrowserTab) -> dict[str, Any]:
    current_url = tab.current_url
    if not current_url:
        try:
            current_url = await scraper._read_page_url(tab.page, tab.browser_type)
        except Exception:
            current_url = tab.target_url
    return {
        "id": tab.session_id,
        "session_id": tab.session_id,
        "target_url": tab.target_url,
        "session_link": current_url,
        "current_url": current_url,
        "browser_type": tab.browser_type,
        "message_count": tab.message_count,
        "in_use": tab.in_use,
        "created_at": tab.created_at.isoformat(),
        "last_used": tab.last_used.isoformat(),
    }


def _openai_completion_payload(result: dict[str, Any], model: str) -> dict[str, Any]:
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    content = result.get("content", "")
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "session_id": result.get("session_id"),
        "session_link": result.get("session_link"),
        "target_url": result.get("target_url"),
        "sources": result.get("sources", []),
    }


def _anthropic_message_payload(result: dict[str, Any], model: str) -> dict[str, Any]:
    created = datetime.utcnow().isoformat() + "Z"
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "text",
                "text": result.get("content", ""),
            }
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
        },
        "session_id": result.get("session_id"),
        "session_link": result.get("session_link"),
        "target_url": result.get("target_url"),
        "sources": result.get("sources", []),
        "created_at": created,
    }


async def _stream_openai_response(request: ChatRequest, model: str):
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    session_id: str | None = None
    session_link: str | None = None

    async for chunk in scraper.chat(request):
        payload = json.loads(chunk)
        if payload.get("error"):
            error_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "error",
                    }
                ],
                "error": payload["error"],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return
        session_id = payload.get("session_id") or session_id
        session_link = payload.get("session_link") or session_link
        if payload.get("type") == "delta" and payload.get("content"):
            data = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": payload["content"],
                        },
                        "finish_reason": None,
                    }
                ],
                "session_id": session_id,
                "session_link": session_link,
            }
            yield f"data: {json.dumps(data)}\n\n"
        elif payload.get("type") == "complete":
            data = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
                "session_id": session_id,
                "session_link": payload.get("session_link") or session_link,
                "sources": payload.get("sources", []),
            }
            yield f"data: {json.dumps(data)}\n\n"
    yield "data: [DONE]\n\n"


async def _stream_anthropic_response(request: ChatRequest, model: str):
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    session_id: str | None = None
    session_link: str | None = None
    started = False

    async for chunk in scraper.chat(request):
        payload = json.loads(chunk)
        if payload.get("error"):
            yield "event: error\n"
            yield f"data: {json.dumps({'type': 'error', 'error': {'message': payload['error']}})}\n\n"
            return
        if not started:
            message_start = {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
            yield "event: message_start\n"
            yield f"data: {json.dumps(message_start)}\n\n"
            yield "event: content_block_start\n"
            yield f"data: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            started = True
        session_id = payload.get("session_id") or session_id
        session_link = payload.get("session_link") or session_link
        if payload.get("type") == "delta" and payload.get("content"):
            delta = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": payload["content"]},
            }
            yield "event: content_block_delta\n"
            yield f"data: {json.dumps(delta)}\n\n"
        elif payload.get("type") == "complete":
            yield "event: content_block_stop\n"
            yield f"data: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            message_delta = {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
                "session_id": session_id,
                "session_link": payload.get("session_link") or session_link,
                "sources": payload.get("sources", []),
            }
            yield "event: message_delta\n"
            yield f"data: {json.dumps(message_delta)}\n\n"
            yield "event: message_stop\n"
            yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scraper
    scraper = BrowserChatScraper(sessions)
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
    return await _collect_chat_result(request)


@app.post("/v1/sessions")
async def create_session(request: SessionCreateRequest):
    profile = BrowserChatProfile.from_request(request.to_chat_request())
    tab = await sessions.get_session(
        request_session_id=request.session_id,
        headless=request.headless,
        profile=profile,
    )
    try:
        return await _session_info(tab)
    finally:
        sessions.release(tab.session_id)


@app.get("/v1/sessions")
async def list_sessions():
    items = [await _session_info(tab) for tab in sessions.sessions.values()]
    return {"object": "list", "data": items}


@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str):
    tab = sessions.sessions.get(session_id)
    if not tab:
        raise HTTPException(404, f"Unknown session_id: {session_id}")
    return await _session_info(tab)


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: OpenAIChatCompletionRequest):
    bridge_request = _chat_request_from_openai(request)
    if request.stream:
        return StreamingResponse(
            _stream_openai_response(bridge_request, request.model),
            media_type="text/event-stream",
        )
    result = await _collect_chat_result(bridge_request)
    return _openai_completion_payload(result, request.model)


@app.post("/v1/messages")
async def anthropic_messages(request: AnthropicMessagesRequest):
    bridge_request = _chat_request_from_anthropic(request)
    if request.stream:
        return StreamingResponse(
            _stream_anthropic_response(bridge_request, request.model),
            media_type="text/event-stream",
        )
    result = await _collect_chat_result(bridge_request)
    return _anthropic_message_payload(result, request.model)


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
        "browser_window_mode": DEFAULT_BROWSER_WINDOW_MODE,
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
