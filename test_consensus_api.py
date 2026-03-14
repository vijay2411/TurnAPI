# test_consensus_api.py - Unit tests for the API layer
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from consensus_api import (
    AuthenticationRequiredError,
    AnthropicMessagesRequest,
    BrowserChatScraper,
    BrowserTargetAdapter,
    DEFAULT_BROWSER_DRIVER,
    DEFAULT_BROWSER_CDP_URL,
    DEFAULT_BROWSER_MODE,
    DEFAULT_BROWSER_PROFILE_DIR,
    DEFAULT_BROWSER_WINDOW_MODE,
    DEFAULT_SUBMIT_SELECTORS,
    DEFAULT_TARGET_URL,
    app,
    BlockingPageError,
    BrowserChatProfile,
    BrowserTab,
    ChatRequest,
    ChatResponse,
    CaptchaHandler,
    CONSENSUS_TARGET_ADAPTER,
    ConsensusScraper,
    GENERIC_TARGET_ADAPTER,
    OpenAIChatCompletionRequest,
    SessionCreateRequest,
    ZAI_TARGET_ADAPTER,
    _chat_request_from_anthropic,
    _chat_request_from_openai,
    _extract_text_from_message_content,
    _last_user_message,
    SessionManager,
    sessions,
    resolve_target_adapter,
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
    assert req.session_link is None


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


def test_browser_chat_profile_uses_session_link_as_target():
    req = ChatRequest(message="hello", session_link="https://example.com/conversation/123")
    profile = BrowserChatProfile.from_request(req)
    assert profile.target_url == "https://example.com/conversation/123"
    assert profile.adapter is GENERIC_TARGET_ADAPTER


def test_session_create_request_to_chat_request():
    req = SessionCreateRequest(session_link="https://example.com/chat/1", target_url="https://ignored.example.com")
    chat_request = req.to_chat_request()
    assert chat_request.session_link == "https://example.com/chat/1"
    assert chat_request.target_url == "https://ignored.example.com"


def test_extract_text_from_message_content():
    assert _extract_text_from_message_content("hello") == "hello"
    assert _extract_text_from_message_content([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"


def test_last_user_message_uses_last_user_turn():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ignore"},
        {"role": "user", "content": [{"type": "text", "text": "second"}]},
    ]
    assert _last_user_message(messages) == "second"


def test_openai_request_bridge():
    req = OpenAIChatCompletionRequest(messages=[{"role": "user", "content": "hello"}], session_link="https://x")
    chat_request = _chat_request_from_openai(req)
    assert chat_request.message == "hello"
    assert chat_request.session_link == "https://x"


def test_anthropic_request_bridge():
    req = AnthropicMessagesRequest(messages=[{"role": "user", "content": "hello"}], session_id="abc")
    chat_request = _chat_request_from_anthropic(req)
    assert chat_request.message == "hello"
    assert chat_request.session_id == "abc"


def test_browser_chat_profile_uses_consensus_adapter_by_default():
    profile = BrowserChatProfile.from_request(ChatRequest(message="hello"))
    assert profile.adapter.name == "consensus"


def test_resolve_target_adapter_falls_back_to_generic():
    adapter = resolve_target_adapter("https://example.com/chat")
    assert adapter is GENERIC_TARGET_ADAPTER
    assert isinstance(adapter, BrowserTargetAdapter)


def test_resolve_target_adapter_for_consensus_url():
    adapter = resolve_target_adapter("https://consensus.app/search/")
    assert adapter is CONSENSUS_TARGET_ADAPTER


def test_resolve_target_adapter_for_zai_url():
    adapter = resolve_target_adapter("https://chat.z.ai/")
    assert adapter is ZAI_TARGET_ADAPTER


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
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x"))
    page = AsyncMock()
    page.title = AsyncMock(return_value="Just a moment...")
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="Performing security verification")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(BlockingPageError):
        await scraper._assert_not_blocked(page, "playwright", profile)


@pytest.mark.asyncio
async def test_blocking_completion_detection_from_cloudflare_sources():
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x"))
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
            profile,
        )


@pytest.mark.asyncio
async def test_empty_completion_detection_for_non_chat_page():
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x"))
    page = AsyncMock()
    page.title = AsyncMock(return_value="Consensus")
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="About Us Careers Contact Us")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(RuntimeError):
        await scraper._assert_valid_completion("", [], page, "playwright", profile)


@pytest.mark.asyncio
async def test_auth_wall_detection():
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x"))
    page = AsyncMock()
    page.title = AsyncMock(return_value="Sign Up - Consensus: AI Search Engine for Research")
    page.url = "https://consensus.app/sign-up/?redirect_url=%2F"
    body_locator = MagicMock()
    body_locator.inner_text = AsyncMock(return_value="Create a free account to continue")
    page.locator = MagicMock(return_value=body_locator)

    with pytest.raises(AuthenticationRequiredError):
        await scraper._assert_not_blocked(page, "playwright", profile)


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
    scraper = BrowserChatScraper(SessionManager())
    assert scraper._should_skip_response_text("Results\n\nNEW\n—\n·\n·\n·", CONSENSUS_TARGET_ADAPTER) is True
    assert scraper._should_skip_response_text(
        "Real answer text with actual alphabetic content and citations 1 2 3.",
        CONSENSUS_TARGET_ADAPTER,
    ) is False
    assert scraper._should_skip_response_text("Thought Process", ZAI_TARGET_ADAPTER) is True
    assert scraper._should_skip_response_text(
        "Quality sleep is essential for cognitive function and emotional well-being.",
        ZAI_TARGET_ADAPTER,
    ) is False
    assert scraper._should_skip_response_text("RED", ZAI_TARGET_ADAPTER) is False


def test_same_turn_detection():
    previous = "Caffeine typically reduces sleep quantity and depth."
    assert CONSENSUS_TARGET_ADAPTER.looks_like_same_turn(previous, previous) is True
    assert CONSENSUS_TARGET_ADAPTER.looks_like_same_turn(
        previous, "Caffeine typically reduces sleep quantity and depth. More detail."
    ) is True
    assert CONSENSUS_TARGET_ADAPTER.looks_like_same_turn(
        previous, "Adolescent caffeine use is associated with shorter sleep."
    ) is False


@pytest.mark.asyncio
async def test_snapshot_existing_response_waits_for_stable_latest():
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x", session_link="https://chat.z.ai/c/example"))
    page = AsyncMock()
    values = iter(["older", "latest", "latest", "latest"])

    async def fake_extract(_page, _profile):
        return next(values)

    scraper._extract_playwright_response = fake_extract
    stable = await scraper._snapshot_existing_response(page, "playwright", profile)
    assert stable == "latest"


@pytest.mark.asyncio
async def test_snapshot_existing_response_can_require_nonempty():
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x", session_link="https://chat.z.ai/c/example"))
    page = AsyncMock()
    values = iter(["", "", "older", "latest", "latest", "latest"])

    async def fake_extract(_page, _profile):
        return next(values)

    scraper._extract_playwright_response = fake_extract
    stable = await scraper._snapshot_existing_response(page, "playwright", profile, require_nonempty=True)
    assert stable == "latest"


@pytest.mark.asyncio
async def test_wait_for_resume_ready_waits_past_loading():
    scraper = BrowserChatScraper(SessionManager())
    profile = BrowserChatProfile.from_request(ChatRequest(message="x", session_link="https://chat.z.ai/c/example"))
    page = AsyncMock()
    states = iter([
        ("title", "Loading..."),
        ("title", "Loading..."),
        ("title", "Conversation ready"),
        ("title", "Conversation ready"),
    ])
    responses = iter(["", "", "latest", "latest"])

    async def fake_state(_page, _browser_type):
        return next(states)

    async def fake_extract(_page, _profile):
        return next(responses)

    scraper._read_page_state = fake_state
    scraper._extract_playwright_response = fake_extract
    await scraper._wait_for_resume_ready(page, "playwright", profile)


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
        assert data["browser_window_mode"] == DEFAULT_BROWSER_WINDOW_MODE
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
            assert "session_link" in data


def test_create_session_endpoint():
    with TestClient(app) as client:
        fake_page = AsyncMock()
        fake_tab = BrowserTab(
            session_id="sess1",
            page=fake_page,
            browser_type="playwright",
            target_url="https://example.com/chat",
            current_url="https://example.com/chat/1",
            in_use=True,
        )
        with patch.object(sessions, "get_session", AsyncMock(return_value=fake_tab)):
            resp = client.post("/v1/sessions", json={"target_url": "https://example.com/chat"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["session_id"] == "sess1"
            assert data["session_link"] == "https://example.com/chat/1"


def test_list_sessions_endpoint():
    with TestClient(app) as client:
        fake_page = AsyncMock()
        fake_tab = BrowserTab(
            session_id="sess2",
            page=fake_page,
            browser_type="playwright",
            target_url="https://example.com/chat",
            current_url="https://example.com/chat/2",
            in_use=False,
        )
        original_sessions = sessions.sessions
        sessions.sessions = {"sess2": fake_tab}
        try:
            resp = client.get("/v1/sessions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["object"] == "list"
            assert data["data"][0]["session_id"] == "sess2"
        finally:
            sessions.sessions = original_sessions


def test_get_session_endpoint_404():
    with TestClient(app) as client:
        resp = client.get("/v1/sessions/missing")
        assert resp.status_code == 404


def test_openai_chat_completions_sync_endpoint():
    with TestClient(app) as client:
        with patch.object(
            BrowserChatScraper,
            "chat",
            return_value=_async_gen([
                json.dumps({
                    "type": "complete",
                    "content": "Hello world",
                    "sources": [],
                    "session_id": "sess3",
                    "session_link": "https://example.com/chat/3",
                    "target_url": "https://example.com/chat",
                }),
            ]),
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "browser-chat", "messages": [{"role": "user", "content": "hello"}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["object"] == "chat.completion"
            assert data["choices"][0]["message"]["content"] == "Hello world"
            assert data["session_id"] == "sess3"


def test_openai_chat_completions_stream_endpoint():
    with TestClient(app) as client:
        with patch.object(
            BrowserChatScraper,
            "chat",
            return_value=_async_gen([
                json.dumps({"type": "delta", "content": "Hello ", "session_id": "sess4"}),
                json.dumps({
                    "type": "complete",
                    "content": "Hello world",
                    "sources": [],
                    "session_id": "sess4",
                    "session_link": "https://example.com/chat/4",
                }),
            ]),
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "browser-chat", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            assert "chat.completion.chunk" in resp.text
            assert "[DONE]" in resp.text


def test_anthropic_messages_sync_endpoint():
    with TestClient(app) as client:
        with patch.object(
            BrowserChatScraper,
            "chat",
            return_value=_async_gen([
                json.dumps({
                    "type": "complete",
                    "content": "Hi there",
                    "sources": [],
                    "session_id": "sess5",
                    "session_link": "https://example.com/chat/5",
                    "target_url": "https://example.com/chat",
                }),
            ]),
        ):
            resp = client.post(
                "/v1/messages",
                json={"model": "browser-chat", "messages": [{"role": "user", "content": "hello"}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["type"] == "message"
            assert data["content"][0]["text"] == "Hi there"
            assert data["session_id"] == "sess5"


def test_anthropic_messages_stream_endpoint():
    with TestClient(app) as client:
        with patch.object(
            BrowserChatScraper,
            "chat",
            return_value=_async_gen([
                json.dumps({"type": "delta", "content": "Hi ", "session_id": "sess6"}),
                json.dumps({
                    "type": "complete",
                    "content": "Hi there",
                    "sources": [],
                    "session_id": "sess6",
                    "session_link": "https://example.com/chat/6",
                }),
            ]),
        ):
            resp = client.post(
                "/v1/messages",
                json={"model": "browser-chat", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            assert "message_start" in resp.text
            assert "message_stop" in resp.text


# ==================== Helpers ====================

async def _async_gen(items):
    for item in items:
        yield item
