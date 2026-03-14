# test_consensus_api.py - Unit tests for the API layer
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from consensus_api import (
    AuthenticationRequiredError,
    DEFAULT_BROWSER_DRIVER,
    DEFAULT_BROWSER_CDP_URL,
    DEFAULT_BROWSER_MODE,
    DEFAULT_BROWSER_PROFILE_DIR,
    DEFAULT_SUBMIT_SELECTORS,
    DEFAULT_TARGET_URL,
    app,
    BlockingPageError,
    BrowserChatProfile,
    BrowserTab,
    ChatRequest,
    ChatResponse,
    CaptchaHandler,
    ConsensusScraper,
    SessionManager,
)


# ==================== Model Tests ====================

def test_chat_request_defaults():
    req = ChatRequest(message="test question")
    assert req.message == "test question"
    assert req.session_id is None
    assert req.stream is True
    assert req.headless is False
    assert req.solve_captcha is True
    assert req.target_url is None


def test_chat_request_custom():
    req = ChatRequest(
        message="test", session_id="abc", stream=False,
        headless=True, solve_captcha=False, target_url="https://example.com",
        input_selectors=["textarea"], response_selectors=["article"], submit_selectors=["#search-button"],
    )
    assert req.session_id == "abc"
    assert req.stream is False
    assert req.headless is True
    assert req.solve_captcha is False
    assert req.target_url == "https://example.com"
    assert req.input_selectors == ["textarea"]
    assert req.response_selectors == ["article"]
    assert req.submit_selectors == ["#search-button"]


def test_chat_request_requires_message():
    with pytest.raises(Exception):
        ChatRequest()


def test_chat_response():
    resp = ChatResponse(session_id="abc", response="answer")
    assert resp.session_id == "abc"
    assert resp.sources == []
    assert resp.metadata == {}


def test_browser_chat_profile_uses_request_overrides():
    req = ChatRequest(
        message="hello",
        target_url="https://example.com/chat",
        input_selectors=["textarea"],
        response_selectors=["article"],
        source_selectors=["a[href^='https://']"],
        submit_selectors=["button[type='submit']"],
    )
    profile = BrowserChatProfile.from_request(req)
    assert profile.target_url == "https://example.com/chat"
    assert profile.input_selectors == ["textarea"]
    assert profile.response_selectors == ["article"]
    assert profile.source_selectors == ["a[href^='https://']"]
    assert profile.submit_selectors == ["button[type='submit']"]


# ==================== BrowserTab Tests ====================

def test_browser_tab_touch():
    tab = BrowserTab(session_id="test", page=None, browser_type="nodriver")
    assert tab.message_count == 0
    assert tab.last_response_text == ""
    assert tab.in_use is False
    tab.touch()
    assert tab.message_count == 1


# ==================== SessionManager Tests ====================

@pytest.mark.asyncio
async def test_session_manager_max_sessions():
    sm = SessionManager(max_sessions=1)
    # Mock _create_session to avoid real browser
    live_page = AsyncMock()
    live_page.evaluate = AsyncMock(return_value=2)
    mock_tab = BrowserTab(session_id="t1", page=live_page, browser_type="nodriver", in_use=True)

    async def fake_create(headless, profile, preferred_session_id=None):
        sm.sessions["t1"] = mock_tab
        return mock_tab

    sm._create_session = fake_create

    tab = await sm.get_session(profile=BrowserChatProfile.from_request(ChatRequest(message="x")))
    assert tab.session_id == "t1"

    # Second request should fail (max_sessions=1, tab still in_use)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await sm.get_session(profile=BrowserChatProfile.from_request(ChatRequest(message="x")))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_session_manager_reuse():
    sm = SessionManager(max_sessions=2)

    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=2)
    mock_tab = BrowserTab(
        session_id="t1",
        page=mock_page,
        browser_type="nodriver",
        target_url=DEFAULT_TARGET_URL,
        in_use=False,
    )
    async def fake_create(headless, profile, preferred_session_id=None):
        created = BrowserTab(
            session_id=preferred_session_id or "fresh",
            page=mock_page,
            browser_type="nodriver",
            target_url=DEFAULT_TARGET_URL,
            in_use=True,
        )
        sm.sessions[created.session_id] = created
        return created

    sm.sessions["t1"] = mock_tab
    sm._create_session = fake_create

    tab = await sm.get_session(profile=BrowserChatProfile.from_request(ChatRequest(message="x")))
    assert tab.session_id == "fresh"
    assert tab.in_use is True
    assert tab.session_id != "t1"


@pytest.mark.asyncio
async def test_session_manager_dead_session_removed():
    sm = SessionManager(max_sessions=2)

    # Create a dead session (evaluate raises)
    dead_page = AsyncMock()
    dead_page.evaluate = AsyncMock(side_effect=Exception("dead"))
    dead_tab = BrowserTab(
        session_id="dead",
        page=dead_page,
        browser_type="nodriver",
        target_url=DEFAULT_TARGET_URL,
        in_use=False,
    )
    sm.sessions["dead"] = dead_tab

    # Mock _create_session for fallback
    new_page = AsyncMock()
    new_tab = BrowserTab(
        session_id="new",
        page=new_page,
        browser_type="nodriver",
        target_url=DEFAULT_TARGET_URL,
        in_use=True,
    )

    async def fake_create(headless, profile, preferred_session_id=None):
        sm.sessions["new"] = new_tab
        return new_tab

    sm._create_session = fake_create

    tab = await sm.get_session(profile=BrowserChatProfile.from_request(ChatRequest(message="x")))
    assert tab.session_id == "new"
    assert "dead" not in sm.sessions


@pytest.mark.asyncio
async def test_session_manager_requested_session_reuse():
    sm = SessionManager(max_sessions=2)
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=2)
    tab = BrowserTab(
        session_id="keepme",
        page=page,
        browser_type="nodriver",
        target_url=DEFAULT_TARGET_URL,
        in_use=False,
    )
    sm.sessions["keepme"] = tab

    reused = await sm.get_session(
        request_session_id="keepme",
        profile=BrowserChatProfile.from_request(ChatRequest(message="x")),
    )
    assert reused.session_id == "keepme"
    assert reused.in_use is True


@pytest.mark.asyncio
async def test_session_manager_new_request_does_not_reuse_idle_session():
    sm = SessionManager(max_sessions=3)
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=2)
    idle_tab = BrowserTab(
        session_id="existing",
        page=page,
        browser_type="nodriver",
        target_url=DEFAULT_TARGET_URL,
        in_use=False,
    )
    sm.sessions["existing"] = idle_tab

    async def fake_create(headless, profile, preferred_session_id=None):
        created = BrowserTab(
            session_id="newone",
            page=page,
            browser_type="nodriver",
            target_url=DEFAULT_TARGET_URL,
            in_use=True,
        )
        sm.sessions["newone"] = created
        return created

    sm._create_session = fake_create

    tab = await sm.get_session(profile=BrowserChatProfile.from_request(ChatRequest(message="x")))
    assert tab.session_id == "newone"
    assert "existing" in sm.sessions


@pytest.mark.asyncio
async def test_blocking_page_detection():
    scraper = ConsensusScraper(SessionManager())
    page = AsyncMock()
    page.title = AsyncMock(return_value="Just a moment...")
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="Performing security verification")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(BlockingPageError):
        await scraper._assert_not_blocked(page, "playwright")


@pytest.mark.asyncio
async def test_blocking_completion_detection_from_cloudflare_sources():
    scraper = ConsensusScraper(SessionManager())
    page = AsyncMock()
    page.title = AsyncMock(return_value="")
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(BlockingPageError):
        await scraper._assert_valid_completion(
            "",
            [{"title": "Cloudflare", "url": "https://www.cloudflare.com"}],
            page,
            "playwright",
        )


@pytest.mark.asyncio
async def test_empty_completion_detection_for_non_chat_page():
    scraper = ConsensusScraper(SessionManager())
    page = AsyncMock()
    page.title = AsyncMock(return_value="Consensus")
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="About Us Careers Contact Us")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(RuntimeError):
        await scraper._assert_valid_completion("", [], page, "playwright")


@pytest.mark.asyncio
async def test_auth_wall_detection():
    scraper = ConsensusScraper(SessionManager())
    page = AsyncMock()
    page.title = AsyncMock(return_value="Sign Up - Consensus: AI Search Engine for Research")
    page.url = "https://consensus.app/sign-up/?redirect_url=%2F"
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="Create a free account to continue")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(AuthenticationRequiredError):
        await scraper._assert_not_blocked(page, "playwright")


@pytest.mark.asyncio
async def test_captcha_handler_skips_normal_results_page():
    handler = CaptchaHandler()
    page = AsyncMock()
    page.title = AsyncMock(return_value="What are the effects of caffeine on sleep? - Consensus")
    page.url = "https://consensus.app/search/example"
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="Results KEY TAKEAWAY Meta-analysis")
    page.locator = MagicMock(return_value=body_locator)

    assert await handler.check_and_solve(page, "playwright") is True


def test_skip_placeholder_response_text():
    scraper = ConsensusScraper(SessionManager())
    assert scraper._should_skip_response_text("Results\n\nNEW\n—\n·\n·\n·") is True
    assert scraper._should_skip_response_text("Real answer text with actual alphabetic content and citations 1 2 3.") is False


def test_same_turn_detection():
    scraper = ConsensusScraper(SessionManager())
    previous = "Caffeine typically reduces sleep quantity and depth."
    assert scraper._looks_like_same_turn(previous, previous) is True
    assert scraper._looks_like_same_turn(previous, "Caffeine typically reduces sleep quantity and depth. More detail.") is True
    assert scraper._looks_like_same_turn(previous, "Adolescent caffeine use is associated with shorter sleep.") is False


# ==================== API Endpoint Tests ====================

def test_health_endpoint():
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "sessions" in data
        assert "nodriver" in data
        assert "playwright" in data
        assert data["default_target_url"] == DEFAULT_TARGET_URL
        assert data["browser_driver"] == DEFAULT_BROWSER_DRIVER
        assert data["browser_mode"] == DEFAULT_BROWSER_MODE
        assert data["browser_profile_dir"] == DEFAULT_BROWSER_PROFILE_DIR
        assert data["browser_cdp_url"] == DEFAULT_BROWSER_CDP_URL


def test_root_returns_html():
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Browser Chat Bridge" in resp.text
        assert "New Chat" in resp.text
        assert "Follow-up Mode" in resp.text


def test_chat_endpoint_returns_sse():
    """Test that /chat returns SSE format (mocking the scraper)"""
    with TestClient(app) as client:
        with patch.object(
            ConsensusScraper, 'chat',
            return_value=_async_gen([
                json.dumps({"type": "delta", "content": "Hello", "session_id": "test"}),
                json.dumps({"type": "complete", "content": "Hello world", "sources": [], "session_id": "test"}),
            ])
        ):
            resp = client.post("/chat", json={"message": "test question"})
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

            lines = resp.text.strip().split("\n\n")
            # Should have delta, complete, and [DONE]
            assert len(lines) >= 2
            assert lines[-1] == "data: [DONE]"

            # Parse first SSE event
            first_data = lines[0].replace("data: ", "")
            parsed = json.loads(first_data)
            assert parsed["type"] == "delta"
            assert parsed["content"] == "Hello"


def test_chat_sync_endpoint():
    """Test that /chat/sync returns JSON"""
    with TestClient(app) as client:
        with patch.object(
            ConsensusScraper, 'chat',
            return_value=_async_gen([
                json.dumps({"type": "delta", "content": "Hello "}),
                json.dumps({"type": "complete", "content": "Hello world", "sources": [{"title": "A paper", "url": "https://doi.org/1234"}], "session_id": "test"}),
            ])
        ):
            resp = client.post("/chat/sync", json={"message": "test question"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["content"] == "Hello world"
            assert len(data["sources"]) == 1
            assert data["target_url"] == DEFAULT_TARGET_URL


# ==================== Helpers ====================

async def _async_gen(items):
    for item in items:
        yield item
