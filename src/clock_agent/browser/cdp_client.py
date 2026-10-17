"""Cliente Chrome DevTools Protocol (CDP) y verificador de sesión autenticada en Catawiki."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

AUTH_COOKIE_NAMES: set[str] = {
    "access_token",
    "cw_token",
    "cw_user",
    "user_id",
    "_catawiki_session",
    "remember_user_token",
    "logged_in",
}

AUTH_DOM_SELECTORS: tuple[str, ...] = (
    '[data-testid="user-menu"]',
    '[data-testid="header-user-menu"]',
    '[data-testid="account-menu"]',
    'a[href*="/buyer/"]',
    'a[href*="/my-catawiki"]',
    '[data-authenticated="true"]',
)


class BrowserConnectionError(RuntimeError):
    """Error lanzado cuando no es posible enlazar con el navegador por CDP o se pierde la conexión."""


class SessionNotAuthenticatedError(RuntimeError):
    """Error lanzado cuando el navegador está conectado pero no hay sesión iniciada en Catawiki."""


def build_browser_launch_command(
    *,
    port: int = 9222,
    browser: str = "chrome",
    user_data_dir: str | Path | None = None,
) -> str:
    """
    Genera el comando de PowerShell en Windows para abrir Chrome o Edge con el puerto
    de depuración remota habilitado (CDP) y un perfil persistente.
    """
    profile_dir = (
        str(user_data_dir)
        if user_data_dir is not None
        else r"$env:LOCALAPPDATA\ClockAgent\chrome-profile"
    )
    if browser.lower() == "edge":
        exe = r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"'
    else:
        exe = r'"C:\Program Files\Google\Chrome\Application\chrome.exe"'
    return (
        f'& {exe} --remote-debugging-port={port} '
        f'--user-data-dir="{profile_dir}" https://www.catawiki.com/es/'
    )



class CDPBrowserClient:
    """
    Gestiona la conexión vía Chrome DevTools Protocol (CDP) sobre el navegador
    real del usuario y verifica que exista una sesión autenticada en Catawiki.
    """

    def __init__(
        self,
        *,
        cdp_url: str = "http://127.0.0.1:9222",
        playwright_instance: Playwright | None = None,
    ) -> None:
        self.cdp_url = cdp_url
        self._playwright: Playwright | None = playwright_instance
        self._owns_playwright: bool = playwright_instance is None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def is_connected(self) -> bool:
        """Devuelve True si el enlace CDP con el navegador está activo."""
        if self._browser is None:
            return False
        return bool(self._browser.is_connected())

    @property
    def context(self) -> BrowserContext:
        """Devuelve el contexto activo del navegador o lanza BrowserConnectionError."""
        if not self.is_connected or self._context is None:
            raise BrowserConnectionError(
                f"El navegador no está conectado en {self.cdp_url}. "
                f"Ábralo con: {build_browser_launch_command()}"
            )
        return self._context

    async def connect(self) -> BrowserContext:
        """Enlaza con el navegador del usuario a través del endpoint CDP configurado."""
        try:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = await self._browser.new_context()
            return self._context
        except Exception as exc:
            raise BrowserConnectionError(
                f"No se pudo conectar al navegador en {self.cdp_url}: {exc}. "
                f"Inicie Chrome con: {build_browser_launch_command()}"
            ) from exc

    async def verify_authenticated_session(
        self,
        *,
        page: Page | None = None,
        require_auth: bool = True,
    ) -> bool:
        """
        Verifica que el navegador siga conectado y que la sesión del usuario en Catawiki
        esté autenticada (mediante cookies de sesión o elementos DOM de usuario autenticado).
        """
        ctx = self.context

        # 1. Comprobar cookies del dominio catawiki.com
        try:
            cookies: list[dict[str, Any]] = await ctx.cookies("https://www.catawiki.com")
        except Exception as exc:
            raise BrowserConnectionError(
                f"Se perdió la conexión con el navegador al consultar cookies: {exc}"
            ) from exc

        for cookie in cookies:
            name = str(cookie.get("name", "")).lower()
            value = str(cookie.get("value", "")).strip()
            if name in AUTH_COOKIE_NAMES and value and value.lower() not in {"false", "0", "null"}:
                return True

        # 2. Comprobar indicadores DOM en la página activa o en las pestañas abiertas de Catawiki
        pages_to_check: list[Page] = []
        if page is not None:
            pages_to_check.append(page)
        else:
            pages_to_check.extend(
                p for p in ctx.pages if "catawiki." in (p.url or "").lower()
            )

        for candidate_page in pages_to_check:
            for selector in AUTH_DOM_SELECTORS:
                try:
                    locator = candidate_page.locator(selector)
                    if await locator.count() > 0:
                        return True
                except Exception:
                    continue

        if require_auth:
            raise SessionNotAuthenticatedError(
                "No se detectó una sesión autenticada en Catawiki. "
                "Por favor, inicie sesión manualmente en su navegador antes de activar las pujas."
            )
        return False

    async def new_page(self) -> Page:
        """Abre una nueva pestaña dentro del contexto autenticado del usuario."""
        ctx = self.context
        try:
            return await ctx.new_page()
        except Exception as exc:
            raise BrowserConnectionError(
                f"Fallo al abrir pestaña en el navegador CDP: {exc}"
            ) from exc

    async def close(self) -> None:
        """Cierra el cliente Playwright local sin cerrar las pestañas del usuario."""
        self._context = None
        self._browser = None
        if self._owns_playwright and self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
