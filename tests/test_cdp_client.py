"""Tests para CDPBrowserClient: conexión exitosa, caída de navegador y sesión no autenticada."""

from typing import Any

import pytest

from chronobid.browser.cdp_client import (
    BrowserConnectionError,
    CDPBrowserClient,
    SessionNotAuthenticatedError,
    build_browser_launch_command,
)


class FakeLocator:
    def __init__(self, count_val: int) -> None:
        self._count_val = count_val

    async def count(self) -> int:
        return self._count_val


class FakePage:
    def __init__(self, url: str = "https://www.catawiki.com/es/", auth_dom: bool = False) -> None:
        self.url = url
        self._auth_dom = auth_dom

    def locator(self, selector: str) -> FakeLocator:
        if self._auth_dom and selector == '[data-testid="user-menu"]':
            return FakeLocator(1)
        return FakeLocator(0)


class FakeBrowserContext:
    def __init__(
        self,
        cookies: list[dict[str, Any]] | None = None,
        pages: list[FakePage] | None = None,
    ) -> None:
        self._cookies = cookies or []
        self.pages = pages or []

    async def cookies(self, urls: str | list[str] | None = None) -> list[dict[str, Any]]:
        return self._cookies

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, context: FakeBrowserContext, connected: bool = True) -> None:
        self.contexts = [context]
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected


class FakeChromium:
    def __init__(self, browser: FakeBrowser | None = None, fail_connect: bool = False) -> None:
        self._browser = browser
        self._fail_connect = fail_connect

    async def connect_over_cdp(self, endpoint_url: str) -> FakeBrowser:
        if self._fail_connect or self._browser is None:
            raise RuntimeError(f"Connection refused at {endpoint_url}")
        return self._browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


async def test_cdp_client_successful_connection_and_cookie_auth() -> None:
    ctx = FakeBrowserContext(
        cookies=[{"name": "cw_user", "value": "user_998877", "domain": ".catawiki.com"}]
    )
    browser = FakeBrowser(ctx, connected=True)
    pw = FakePlaywright(FakeChromium(browser=browser))

    client = CDPBrowserClient(
        cdp_url="http://127.0.0.1:9222",
        playwright_instance=pw,  # type: ignore[arg-type]
    )
    await client.connect()
    assert client.is_connected is True
    assert await client.verify_authenticated_session() is True


async def test_cdp_client_authenticated_via_dom_selector() -> None:
    page = FakePage(url="https://www.catawiki.com/es/l/123456", auth_dom=True)
    ctx = FakeBrowserContext(cookies=[], pages=[page])
    browser = FakeBrowser(ctx, connected=True)
    pw = FakePlaywright(FakeChromium(browser=browser))

    client = CDPBrowserClient(playwright_instance=pw)  # type: ignore[arg-type]
    await client.connect()
    assert await client.verify_authenticated_session() is True


async def test_cdp_client_raises_when_browser_unavailable_or_drops() -> None:
    pw_fail = FakePlaywright(FakeChromium(fail_connect=True))
    client_fail = CDPBrowserClient(playwright_instance=pw_fail)  # type: ignore[arg-type]

    with pytest.raises(BrowserConnectionError, match="No se pudo conectar"):
        await client_fail.connect()

    # Conexión que se cae después de enlazar
    ctx = FakeBrowserContext(cookies=[{"name": "cw_user", "value": "123"}])
    browser = FakeBrowser(ctx, connected=True)
    pw_drop = FakePlaywright(FakeChromium(browser=browser))
    client_drop = CDPBrowserClient(playwright_instance=pw_drop)  # type: ignore[arg-type]
    await client_drop.connect()
    assert client_drop.is_connected is True

    browser._connected = False
    assert client_drop.is_connected is False
    with pytest.raises(BrowserConnectionError, match="no está conectado"):
        await client_drop.verify_authenticated_session()


async def test_cdp_client_raises_when_session_not_authenticated() -> None:
    page = FakePage(url="https://www.catawiki.com/es/", auth_dom=False)
    ctx = FakeBrowserContext(cookies=[], pages=[page])
    browser = FakeBrowser(ctx, connected=True)
    pw = FakePlaywright(FakeChromium(browser=browser))

    client = CDPBrowserClient(playwright_instance=pw)  # type: ignore[arg-type]
    await client.connect()

    assert await client.verify_authenticated_session(require_auth=False) is False
    with pytest.raises(SessionNotAuthenticatedError, match="sesión autenticada"):
        await client.verify_authenticated_session(require_auth=True)


def test_build_browser_launch_command() -> None:
    cmd_chrome = build_browser_launch_command(port=9222, browser="chrome")
    assert "--remote-debugging-port=9222" in cmd_chrome
    assert "chrome.exe" in cmd_chrome

    cmd_edge = build_browser_launch_command(port=9333, browser="edge")
    assert "--remote-debugging-port=9333" in cmd_edge
    assert "msedge.exe" in cmd_edge
