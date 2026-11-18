"""The free audit's browser (www.likyly.com/audit): renders a page the way a visitor's browser does,
for the recommendation blocks that only exist once JavaScript has run.

    POST /render  {url, timeout_ms?}  ->  {status, url, html, ms}   (the DOM after rendering)
    GET  /health

Runs in its own container, apart from the API, with a hard memory cap: a page that exhausts it takes
down this container only. One page at a time, a fresh browser for each (nothing kept between
audits, no memory used when idle). Its only way out is egress_proxy.py - public websites, ports
80/443 - so a page can't make it reach the API, the databases or the cloud metadata service.

It visits as the audit's robot - the same honest user agent - and never works around a site that
refuses robots: a 401/403/429/503 is reported as is.
"""
import asyncio
import logging
import os
import re
import time
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from playwright.async_api import Browser, Error as PlaywrightError, Playwright, TimeoutError as PlaywrightTimeout, async_playwright
from pydantic import BaseModel, Field

import egress_proxy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("renderer")

USER_AGENT = "Mozilla/5.0 (compatible; LikylyAudit/1.0; +https://www.likyly.com/audit) rendering"
MAX_HTML_CHARS = 6 * 1024 * 1024
WAIT_FOR_SLOT_SECONDS = 10
BLOCKED_RESOURCES = {"image", "media", "font"}  # not needed to read the blocks, and the heaviest
# Advertising and analytics: no recommendation block comes from them, and they cost most of the
# memory and CPU of a page. Recommendation vendors (Nosto, Algolia, Taboola...) are not listed.
BLOCKED_HOSTS = re.compile(
    r"(^|\.)(doubleclick\.net|googlesyndication\.com|googleadservices\.com|google-analytics\.com|googletagmanager\.com|"
    r"adservice\.google\.[a-z.]+|amazon-adsystem\.com|adnxs\.com|criteo\.(com|net)|facebook\.net|connect\.facebook\.net|"
    r"hotjar\.com|clarity\.ms|bing\.com|tiktok\.com|snapchat\.com|pinterest\.com|linkedin\.com|twitter\.com|x\.com|"
    r"scorecardresearch\.com|quantserve\.com|moatads\.com|adsrvr\.org|rubiconproject\.com|pubmatic\.com|casalemedia\.com|"
    r"smartadserver\.com|teads\.tv|outbrain-ads\.com|youtube\.com|ytimg\.com|vimeo\.com|dailymotion\.com)$",
    re.IGNORECASE,
)
# The page a site puts in front of robots it doesn't let through. The audit stops there: it never
# waits for such a page to let it pass.
CHALLENGE_RE = re.compile(
    r"<title>\s*(just a moment|attention required|client challenge|access denied|pardon our interruption|"
    r"are you a (robot|human)|vérification|un instant|security check|bot verification|one more step)|"
    r"challenge-platform|cf-chl-|_fs-ch-|px-captcha|captcha-delivery\.com|geo\.captcha|/_Incapsula_Resource|hcaptcha\.com/1/api|"
    r"recaptcha/(api|enterprise)\.js\?render=explicit",
    re.IGNORECASE,
)
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--no-pings",
    "--mute-audio",
    # One renderer process for the page and its frames (no process per site): the memory cap is
    # what matters here, not isolating third-party frames from a page we only read.
    "--renderer-process-limit=1",
    "--disable-features=IsolateOrigins,site-per-process,Translate,MediaRouter,OptimizationHints,AcceptCHFrame",
    "--disable-site-isolation-trials",
    "--js-flags=--max-old-space-size=192",
    # WebRTC is the one way out that can skip a proxy: no UDP that doesn't go through it.
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
]
CHROMIUM_ARGS += [arg for arg in os.environ.get("RENDERER_EXTRA_ARGS", "").split() if arg.startswith("--")]

# Scrolls down to the bottom (blocks that load when they come into view), then back up.
SCROLL_SCRIPT = """async () => {
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0, y = 0; i < 12 && y < 24000; i++) {
    y += Math.round(window.innerHeight * 0.9);
    window.scrollTo(0, y);
    await pause(160);
    if (y >= document.documentElement.scrollHeight) break;
  }
  await pause(300);
  window.scrollTo(0, 0);
}"""


class RenderRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    timeout_ms: int = Field(15000, ge=3000, le=25000)


state: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    proxy = await egress_proxy.start()
    state["proxy_port"] = proxy.sockets[0].getsockname()[1]
    state["slot"] = asyncio.Semaphore(1)
    log.info("egress proxy on 127.0.0.1:%s", state["proxy_port"])
    try:
        yield
    finally:
        proxy.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    return {"ok": True}


async def _route(route):
    request = route.request
    if request.resource_type in BLOCKED_RESOURCES or BLOCKED_HOSTS.search(urlsplit(request.url).hostname or ""):
        await route.abort()
    else:
        await route.continue_()


async def render(url: str, timeout_ms: int) -> dict:
    started = time.monotonic()
    deadline = started + timeout_ms / 1000

    def left(cap_ms: int) -> int:
        return max(500, min(cap_ms, int((deadline - time.monotonic()) * 1000)))

    # The driver and the browser start for this page and stop after it: nothing stays in memory
    # between audits.
    playwright: Playwright = await async_playwright().start()
    browser: Optional[Browser] = None
    try:
        browser = await playwright.chromium.launch(
            headless=True,
            args=CHROMIUM_ARGS,
            # No bypass at all: Chromium otherwise goes direct for localhost.
            proxy={"server": f"http://127.0.0.1:{state['proxy_port']}", "bypass": "<-loopback>"},
        )
        context = await browser.new_context(
            user_agent=USER_AGENT, locale="fr-FR", viewport={"width": 1366, "height": 900},
            service_workers="block", accept_downloads=False,
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
        )
        await context.route("**/*", _route)
        page = await context.new_page()
        page.on("popup", lambda popup: asyncio.ensure_future(popup.close()))
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=left(timeout_ms))
        except PlaywrightTimeout:
            raise HTTPException(status_code=504, detail="timeout")
        except PlaywrightError as error:
            raise HTTPException(status_code=502, detail=str(error).splitlines()[0][:200])
        status = response.status if response else 0
        first = await asyncio.wait_for(page.content(), timeout=left(4000) / 1000)
        if CHALLENGE_RE.search(first[:200_000]):
            return {"status": status, "url": page.url, "html": first[:MAX_HTML_CHARS], "challenge": True, "ms": int((time.monotonic() - started) * 1000)}
        if status < 400:
            for step, cap in (
                (lambda: page.wait_for_load_state("load", timeout=left(2500)), 2500),
                (lambda: page.evaluate(SCROLL_SCRIPT), 4000),
                (lambda: page.wait_for_load_state("networkidle", timeout=left(1200)), 1200),
            ):
                if time.monotonic() > deadline - 1.5:
                    break
                try:
                    # a page whose scripts keep the browser busy must not hold the audit
                    await asyncio.wait_for(step(), timeout=left(cap) / 1000 + 0.5)
                except (PlaywrightTimeout, PlaywrightError, asyncio.TimeoutError):
                    pass
        html = await asyncio.wait_for(page.content(), timeout=4)
        return {"status": status, "url": page.url, "html": html[:MAX_HTML_CHARS], "challenge": bool(CHALLENGE_RE.search(html[:200_000])), "ms": int((time.monotonic() - started) * 1000)}
    except (asyncio.TimeoutError, PlaywrightError) as error:
        # the page crashed (memory cap) or stopped answering
        raise HTTPException(status_code=502, detail=f"page: {type(error).__name__}")
    finally:
        if browser is not None:
            try:
                await asyncio.wait_for(browser.close(), timeout=3)
            except (PlaywrightError, asyncio.TimeoutError):
                pass
        # stopping the driver kills a browser that didn't close
        try:
            await asyncio.wait_for(playwright.stop(), timeout=5)
        except (PlaywrightError, asyncio.TimeoutError):
            pass


@app.post("/render")
async def render_endpoint(payload: RenderRequest, x_renderer_token: Optional[str] = Header(None)):
    expected = os.environ.get("RENDERER_TOKEN")
    if expected and x_renderer_token != expected:
        raise HTTPException(status_code=401, detail="token")
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url")
    slot: asyncio.Semaphore = state["slot"]
    try:
        await asyncio.wait_for(slot.acquire(), timeout=WAIT_FOR_SLOT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="busy")
    try:
        try:
            result = await asyncio.wait_for(render(payload.url, payload.timeout_ms), timeout=payload.timeout_ms / 1000 + 12)
        except asyncio.TimeoutError:
            log.warning("gave up on %s", payload.url[:120])
            raise HTTPException(status_code=504, detail="timeout")
        log.info("rendered %s -> %s in %sms (%s chars)", payload.url[:120], result["status"], result["ms"], len(result["html"]))
        return result
    finally:
        slot.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7000")), log_level="warning")
