"""Bounded public-web fetching, with DNS pinning and a constrained browser proxy."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Protocol, cast

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]
MAX_SOURCE_BYTES = 2 * 1024 * 1024


class SourceError(ValueError):
    """An unsafe, unavailable, unsupported, or oversized research source."""


def public_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if (
            len(value) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.port not in {None, 80, 443}
            or url.host.lower().rstrip(".") in {"localhost", "metadata.google.internal"}
        ):
            raise SourceError("Source must be a public HTTP(S) URL on port 80 or 443")
        try:
            address = ipaddress.ip_address(url.host)
        except ValueError:
            pass
        else:
            if not address.is_global or address.is_multicast:
                raise SourceError("Source address is not public")
        return url.copy_with(fragment=None)
    except httpx.InvalidURL:
        raise SourceError("Invalid source URL") from None


async def resolve_public(host: str, port: int) -> tuple[str, ...]:
    async with asyncio.timeout(5):
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
    if not addresses or any(
        not ipaddress.ip_address(value).is_global or ipaddress.ip_address(value).is_multicast
        for value in addresses
    ):
        raise SourceError("Source DNS did not resolve exclusively to public addresses")
    return addresses[:4]


class PublicTransport(httpx.AsyncBaseTransport):
    """Connect to the validated IP while retaining the original Host and TLS identity."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve_public,
    ) -> None:
        self.transport = transport or httpx.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
            http2=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=0),
        )
        self.resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = public_url(str(request.url))
        addresses = await self.resolver(
            url.host, url.port or (443 if url.scheme == "https" else 80)
        )
        if not addresses:
            raise SourceError("No public source addresses")
        # The resolver is injectable only by trusted application code, never task input.
        headers = httpx.Headers(request.headers)
        headers["Connection"] = "close"
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = url.host
        if request.method not in {"GET", "HEAD"}:
            raise SourceError("Research transport permits only GET and HEAD")
        for index, address in enumerate(addresses):
            pinned = httpx.Request(
                request.method,
                url.copy_with(host=address),
                headers=headers,
                stream=request.stream,
                extensions=extensions,
            )
            try:
                return await self.transport.handle_async_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise
        raise SourceError("No reachable public source addresses")

    async def aclose(self) -> None:
        await self.transport.aclose()


@dataclass(frozen=True)
class WebSource:
    url: str
    content: bytes
    content_type: str


class PublicWeb:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def fetch(self, value: str, *, max_bytes: int = MAX_SOURCE_BYTES) -> WebSource:
        url = public_url(value)
        # No cookies, environment proxy credentials, or authentication cross source boundaries.
        async with (
            httpx.AsyncClient(
                transport=self.transport or PublicTransport(),
                trust_env=False,
                timeout=httpx.Timeout(20, connect=5),
                follow_redirects=False,
                headers={"User-Agent": "OpenFoundry/0.1 research", "Accept-Encoding": "identity"},
            ) as client,
            asyncio.timeout(30),
        ):
            for _ in range(6):
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if location is None:
                            raise SourceError("Source redirect has no location")
                        url = public_url(str(url.join(location)))
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise SourceError("Source exceeds the byte limit")
                    return WebSource(
                        str(url), bytes(content), response.headers.get("content-type", "")
                    )
            raise SourceError("Source exceeds the redirect limit")


def html_to_markdown(content: str | bytes, base_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else base_url
    for element in soup(["script", "style", "noscript", "iframe", "form", "svg", "template"]):
        element.decompose()
    for link in soup.find_all("a", href=True):
        href = link.get("href")
        if isinstance(href, str):
            try:
                link["href"] = str(public_url(str(httpx.URL(base_url).join(href))))
            except (ValueError, httpx.InvalidURL):
                del link["href"]
    # Images carry no extracted evidence and can embed tracking/data URLs.
    for image in soup.find_all("img"):
        image.decompose()
    body = soup.find("main") or soup.find("article") or soup.body or soup
    text = markdownify(str(body), heading_style="ATX", strip=["img"]).strip()
    if not text:
        raise SourceError("Source contains no extractable text")
    return title[:255] or base_url[:255], text


class _Page(Protocol):
    async def goto(self, url: str) -> None: ...
    async def evaluate(self, script: str) -> str: ...
    async def get_url(self) -> str: ...


class _Browser(Protocol):
    async def start(self) -> None: ...
    async def new_page(self) -> _Page: ...
    async def kill(self) -> None: ...


class BrowserProxy:
    """Anonymous, same-host HTTP/CONNECT proxy; every connection pins a public IP.

    Browser navigation allowlists alone do not protect subresources against SSRF.
    The browser must send all traffic here, including loopback destinations. No upstream
    proxy, local DNS rebinding, or private subresource requests are permitted.
    """

    def __init__(self, host: str, *, resolver: Resolver = resolve_public) -> None:
        self.host = host
        self.resolver = resolver
        self.clients: set[asyncio.Task[None]] = set()
        self.server: asyncio.Server | None = None
        self.url = ""

    async def __aenter__(self) -> BrowserProxy:
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=16384)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.clients):
            task.cancel()
        await asyncio.gather(*tuple(self.clients), return_exceptions=True)
        self.clients.clear()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.clients) >= 8:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self.clients.add(task)
        task.add_done_callback(self.clients.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(25):
                header = await reader.readuntil(b"\r\n\r\n")
                first, *lines = header.decode("latin-1").split("\r\n")
                method, target, _ = first.split(" ", 2)
                if method == "CONNECT":
                    url = public_url("https://" + target)
                elif method in {"GET", "HEAD"}:
                    url = public_url(target)
                    if url.scheme != "http":
                        raise SourceError("HTTPS requires CONNECT")
                else:
                    raise SourceError("Unsupported browser proxy method")
                if url.host != self.host:
                    raise SourceError("Cross-host browser resources are blocked")
                port = url.port or (443 if url.scheme == "https" else 80)
                addresses = await self.resolver(url.host, port)
                incoming, upstream = await asyncio.open_connection(addresses[0], port, limit=65536)
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    path = url.raw_path.decode("ascii")
                    outgoing = [f"{method} {path} HTTP/1.1", f"Host: {url.netloc.decode()}"]
                    for line in lines:
                        key = line.partition(":")[0].lower()
                        if key in {"user-agent", "accept", "accept-language", "accept-encoding"}:
                            outgoing.append(line)
                    upstream.write(
                        ("\r\n".join(outgoing) + "\r\nConnection: close\r\n\r\n").encode("latin-1")
                    )
                    await upstream.drain()
                await writer.drain()

                async def relay(
                    source: asyncio.StreamReader, destination: asyncio.StreamWriter
                ) -> None:
                    size = 0
                    while chunk := await source.read(65536):
                        size += len(chunk)
                        if size > 8 * MAX_SOURCE_BYTES:
                            raise SourceError("Browser transfer exceeds limit")
                        destination.write(chunk)
                        await destination.drain()

                a = asyncio.create_task(relay(reader, upstream))
                b = asyncio.create_task(relay(incoming, writer))
                try:
                    done, _ = await asyncio.wait({a, b}, return_when=asyncio.FIRST_COMPLETED)
                    for completed in done:
                        completed.result()
                finally:
                    a.cancel()
                    b.cancel()
                    await asyncio.gather(a, b, return_exceptions=True)
        except (
            OSError,
            ValueError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            # No exception details: upstream URLs may contain private query data.
            with contextlib.suppress(OSError):
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                await writer.drain()
        finally:
            if upstream is not None:
                upstream.close()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


async def browser_html(url: str, *, max_bytes: int = MAX_SOURCE_BYTES) -> str:
    """Use browser-use's deterministic browser API, never its unmetered LLM Agent."""
    source = public_url(url)
    module = importlib.import_module("browser_use.browser.session")
    profile_module = importlib.import_module("browser_use.browser.profile")
    await resolve_public(source.host, source.port or 443)
    async with BrowserProxy(source.host) as proxy:
        with TemporaryDirectory(prefix="openfoundry-browser-") as profile_dir:
            profile = profile_module.BrowserProfile(
                headless=True,
                is_local=True,
                use_cloud=False,
                keep_alive=False,
                chromium_sandbox=True,
                disable_security=False,
                user_data_dir=profile_dir,
                enable_default_extensions=False,
                captcha_solver=False,
                allowed_domains=[source.host],
                proxy=profile_module.ProxySettings(server=proxy.url, bypass="<-loopback>"),
                args=[
                    "--proxy-bypass-list=<-loopback>",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
            )
            # Refuse library defaults that disable Chromium's OS sandbox in containers.
            if any(arg == "--no-sandbox" for arg in profile.get_args()):
                raise SourceError("Browser sandbox is unavailable; using HTTP extraction")
            browser = cast(_Browser, module.BrowserSession(browser_profile=profile))
            try:
                async with asyncio.timeout(30):
                    await browser.start()
                    page = await browser.new_page()
                    await page.goto(str(source))
                    # A single browser-side promise waits for DOM readiness without host polling.
                    await page.evaluate(
                        "() => new Promise(resolve => { "
                        "if (document.readyState !== 'loading') return resolve('ready'); "
                        "document.addEventListener('DOMContentLoaded', () => resolve('ready'), "
                        "{once: true}); setTimeout(() => resolve('timeout'), 10000); })"
                    )
                    current = public_url(await page.get_url())
                    if current.host != source.host:
                        raise SourceError("Browser left its source host")
                    result = await page.evaluate(
                        f"() => document.documentElement.outerHTML.slice(0, {max_bytes + 1})"
                    )
                    if len(result.encode()) > max_bytes:
                        raise SourceError("Rendered source exceeds byte limit")
                    return result
            finally:
                # Always reap browser processes, including after task cancellation.
                cleanup = asyncio.create_task(browser.kill())
                try:
                    await asyncio.wait_for(asyncio.shield(cleanup), 10)
                except BaseException:
                    cleanup.cancel()
                    await asyncio.gather(cleanup, return_exceptions=True)
                    raise
