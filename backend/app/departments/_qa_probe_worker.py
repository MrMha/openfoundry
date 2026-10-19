"""Standalone probe runtime copied into a mission and executed by its pytest worker.

No OpenFoundry package imports: generated projects may themselves use a `backend`
package. All generated application imports and optional browsers stay in this process.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib
import inspect
import io
import ipaddress
import json
import os
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, TypedDict, cast
from uuid import uuid4

import httpx
from asgi_lifespan import LifespanManager
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from pydantic import JsonValue
from starlette.types import ASGIApp

LIMIT = 2 * 1024 * 1024


class Probe(TypedDict):
    path: str
    expected_status: int
    contains: str | None
    selectors: list[str]


class Plan(TypedDict):
    asgi_app: str | None
    app_factory: bool
    base_url: str | None
    probes: list[Probe]
    prefer_browser: bool


@dataclass
class Rendered:
    png: bytes
    errors: list[str]
    failures: list[str]
    backend: str
    warnings: list[str]


def validate_dom(html: str, probe: Probe) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    failures: list[str] = []
    if probe["contains"] and probe["contains"] not in soup.get_text(" ", strip=True):
        failures.append("Expected text was absent from the response DOM")
    for selector in probe["selectors"]:
        if not soup.select(selector):
            failures.append(f"Missing DOM selector: {selector[:200]}")
    return failures


def diagnostic_frame(url: str, status: int | None, html: str, failures: list[str]) -> bytes:
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 48), fill="#8b2635" if failures else "#165b40")
    draw.text((16, 16), "HTTP / ASGI DOM DIAGNOSTIC - NOT A BROWSER SCREENSHOT", fill="white")
    soup = BeautifulSoup(html, "html.parser")
    lines = [
        f"URL: {url[:140]}",
        f"HTTP status: {status}",
        "JavaScript was not executed in this fallback.",
        "",
        *failures[:8],
        "",
        "DOM evidence:",
    ]
    lines.extend(
        f"<{tag.name}> {tag.get_text(' ', strip=True)[:100]}"
        for tag in soup.find_all(["title", "h1", "h2", "p", "button", "a", "input", "form"])[:15]
    )
    for index, line in enumerate(lines[:26]):
        draw.text((16, 64 + index * 17), line, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def _playwright(html: str, probe: Probe) -> Rendered:
    module = importlib.import_module("playwright.async_api")
    errors: list[str] = []
    async with module.async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            chromium_sandbox=True,
            proxy={"server": "http://127.0.0.1:9", "bypass": "<-loopback>"},
            args=[
                "--disable-quic",
                "--proxy-bypass-list=<-loopback>",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        try:
            context = await browser.new_context(viewport={"width": 960, "height": 540})
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            page.on(
                "pageerror",
                lambda error: errors.append(str(error)[:4096]) if len(errors) < 100 else None,
            )
            page.on(
                "console",
                lambda message: (
                    errors.append(str(message.text)[:4096])
                    if message.type == "error" and len(errors) < 100
                    else None
                ),
            )
            await page.set_content(html, wait_until="domcontentloaded", timeout=10000)
            snapshot: str = await page.content()
            screenshot: bytes = await page.screenshot(type="png", timeout=10000)
            if len(screenshot) > LIMIT:
                raise ValueError("Browser screenshot exceeds the artifact limit")
            return Rendered(
                screenshot,
                errors,
                validate_dom(snapshot, probe),
                "playwright-dom",
                ["Browser rendered the fetched response offline; external resources were blocked."],
            )
        finally:
            await asyncio.wait_for(browser.close(), 3)


class BrowserPage(Protocol):
    async def evaluate(self, script: str, *args: object) -> str: ...
    async def screenshot(self, format: str = "png") -> str: ...


class Browser(Protocol):
    async def start(self) -> None: ...
    async def new_page(self) -> BrowserPage: ...
    async def kill(self) -> None: ...


async def _browser_use(html: str, probe: Probe) -> Rendered:
    session = importlib.import_module("browser_use.browser.session")
    profiles = importlib.import_module("browser_use.browser.profile")
    with TemporaryDirectory(prefix="openfoundry-qa-browser-") as directory:
        profile = profiles.BrowserProfile(
            headless=True,
            chromium_sandbox=True,
            disable_security=False,
            is_local=True,
            use_cloud=False,
            keep_alive=False,
            user_data_dir=directory,
            enable_default_extensions=False,
            captcha_solver=False,
            proxy=profiles.ProxySettings(server="http://127.0.0.1:9", bypass="<-loopback>"),
            args=[
                "--disable-quic",
                "--proxy-bypass-list=<-loopback>",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        if "--no-sandbox" in profile.get_args():
            raise RuntimeError("Browser sandbox is unavailable")
        browser = cast(Browser, session.BrowserSession(browser_profile=profile))
        try:
            await browser.start()
            page = await browser.new_page()
            await page.evaluate(
                "(html) => {window.__qaErrors=[]; const record=e => {"
                "if(window.__qaErrors.length<100) "
                "window.__qaErrors.push(String(e).slice(0,4096));}; "
                "window.addEventListener('error', e => record(e.message)); "
                "const original=console.error.bind(console); console.error=(...args)=>{"
                "record(args.map(String).join(' ')); original(...args);}; document.open(); "
                "document.write(html); document.close(); return 'ready';}",
                html,
            )
            snapshot = await page.evaluate("() => document.documentElement.outerHTML")
            errors = json.loads(
                await page.evaluate("() => JSON.stringify(window.__qaErrors || [])")
            )
            screenshot = base64.b64decode(await page.screenshot(), validate=True)
            if len(screenshot) > LIMIT:
                raise ValueError("Browser screenshot exceeds the artifact limit")
            return Rendered(
                screenshot,
                [str(error)[:4096] for error in errors[:100]],
                validate_dom(snapshot, probe),
                "browser-use-dom",
                ["Browser rendered the fetched response offline; external resources were blocked."],
            )
        finally:
            await asyncio.wait_for(browser.kill(), 3)


async def render_response(
    html: str,
    probe: Probe,
    url: str,
    status: int | None,
    prefer_browser: bool,
) -> Rendered:
    warnings: list[str] = []
    if prefer_browser:
        for renderer in (_playwright, _browser_use):
            try:
                async with asyncio.timeout(15):
                    return await renderer(html, probe)
            except Exception as error:
                warnings.append(
                    f"{renderer.__name__.lstrip('_')} unavailable ({type(error).__name__})"
                )
    failures = validate_dom(html, probe)
    return Rendered(
        diagnostic_frame(url, status, html, failures),
        [],
        failures,
        "http-asgi-dom",
        [*warnings, "DOM-only fallback; JavaScript and browser interaction were not tested."],
    )


async def _addresses(url: httpx.URL) -> tuple[str, ...]:
    host = url.host
    async with asyncio.timeout(5):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
    explicit_loopback = host == "localhost"
    with contextlib.suppress(ValueError):
        explicit_loopback = ipaddress.ip_address(host).is_loopback
    if not addresses or any(
        (
            not ipaddress.ip_address(value).is_loopback
            if explicit_loopback
            else not ipaddress.ip_address(value).is_global
            or ipaddress.ip_address(value).is_multicast
        )
        for value in addresses
    ):
        raise ValueError(
            "QA destination did not resolve to its allowed public/loopback address class"
        )
    return addresses[:4]


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.transport = httpx.AsyncHTTPTransport(
            trust_env=False, retries=0, limits=httpx.Limits(max_keepalive_connections=0)
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = await _addresses(request.url)
        for index, address in enumerate(addresses):
            extensions = {**request.extensions, "sni_hostname": request.url.host}
            headers = httpx.Headers(request.headers)
            headers["Connection"] = "close"
            pinned = httpx.Request(
                "GET",
                request.url.copy_with(host=address),
                headers=headers,
                stream=request.stream,
                extensions=extensions,
            )
            try:
                return await self.transport.handle_async_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise
        raise ValueError("No reachable QA destination")

    async def aclose(self) -> None:
        await self.transport.aclose()


@asynccontextmanager
async def probe_client(plan: Plan) -> AsyncIterator[httpx.AsyncClient]:
    reference = plan["asgi_app"]
    if reference:
        module, name = reference.split(":", 1)
        application: object = getattr(importlib.import_module(module), name)
        if plan["app_factory"]:
            if not callable(application):
                raise TypeError("ASGI factory is not callable")
            application = application()
            if inspect.isawaitable(application):
                application = await application
        app = cast(ASGIApp, application)
        async with LifespanManager(app, startup_timeout=10, shutdown_timeout=5) as manager:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app),
                base_url="http://asgi.local",
                timeout=10,
            ) as client:
                yield client
    else:
        async with httpx.AsyncClient(
            transport=PinnedTransport(),
            base_url=plan["base_url"] or "http://asgi.local",
            trust_env=False,
            timeout=10,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        ) as client:
            yield client


def _safe_path(relative: str, *, create: bool = False) -> Path:
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in relative:
        raise ValueError("Worker artifacts require contained relative paths")
    root = Path.cwd().resolve()
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise ValueError("Worker artifact paths cannot traverse links")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Worker artifact path escaped its workspace")
    if create:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def _write(relative: str, content: bytes) -> None:
    if len(content) > LIMIT:
        raise ValueError("Worker artifact exceeds its byte limit")
    target = _safe_path(relative, create=True)
    temporary = target.with_name(".openfoundry-write-" + uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
        _safe_path(relative)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


async def run_probe_worker(plan_path: str, result_path: str, screenshot_prefix: str) -> None:
    plan = cast(Plan, json.loads(_safe_path(plan_path).read_text(encoding="utf-8")))
    results: list[JsonValue] = []
    client_error: str | None = None
    try:
        async with probe_client(plan) as client:
            for index, probe in enumerate(plan["probes"]):
                started = time.monotonic()
                html = ""
                status: int | None = None
                url = str(client.base_url.join(probe["path"]))
                errors: list[str] = []
                try:
                    async with asyncio.timeout(20), client.stream("GET", probe["path"]) as response:
                        status = response.status_code
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            body.extend(chunk)
                            if len(body) > LIMIT:
                                raise ValueError("Endpoint response exceeds byte limit")
                        html = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    if status != probe["expected_status"]:
                        errors.append(
                            f"Expected HTTP {probe['expected_status']}, received {status}"
                        )
                    rendered = await render_response(
                        html, probe, url, status, plan["prefer_browser"]
                    )
                    errors.extend(rendered.failures)
                    errors.extend(rendered.errors)
                except Exception as error:
                    errors.append(
                        f"Endpoint probe failed ({type(error).__name__}): {str(error)[:1000]}"
                    )
                    rendered = Rendered(
                        diagnostic_frame(url, status, html, errors), [], [], "http-asgi-dom", []
                    )
                frame_path = f"{screenshot_prefix}/{index}.png"
                _write(frame_path, rendered.png)
                results.append(
                    {
                        "path": probe["path"],
                        "url": url,
                        "status": status,
                        "passed": not errors,
                        "failures": list(errors),
                        "backend": rendered.backend,
                        "warnings": list(rendered.warnings),
                        "console_errors": list(rendered.errors),
                        "frame_path": frame_path,
                        "duration_seconds": time.monotonic() - started,
                    }
                )
    except Exception as error:
        client_error = (
            f"Application startup/probe failed ({type(error).__name__}): {str(error)[:1000]}"
        )
    _write(
        result_path,
        json.dumps({"results": results, "error": client_error}, ensure_ascii=False).encode(),
    )
