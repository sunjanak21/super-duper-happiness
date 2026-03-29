"""Async HTTP fetcher for downloading mutual fund factsheet PDFs from Indian AMC websites."""

import asyncio
import logging
import os
import random
import re
import urllib.parse
import urllib.robotparser
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import httpx

from models.schema import DownloadResult, DownloadStatus

logger = logging.getLogger(__name__)

# Rotating user-agent strings to reduce chance of blocking.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.0.0",
]

# Per-domain timestamps for rate limiting.
_domain_last_request: dict[str, float] = {}
_domain_locks: dict[str, asyncio.Lock] = {}


def _random_ua() -> str:
    return random.choice(USER_AGENTS)


# ---------------------------------------------------------------------------
# HTML link parser using stdlib html.parser
# ---------------------------------------------------------------------------

class _LinkExtractor(HTMLParser):
    """Extract <a href="..."> links along with their inner text."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, text)
        self._current_href: Optional[str] = None
        self._current_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_href = href
                self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            text = " ".join(self._current_text_parts).strip()
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text_parts = []

    def error(self, message: str) -> None:  # pragma: no cover – required override
        logger.debug("HTMLParser error: %s", message)


def _matches_css_like_selector(href: str, text: str, selector: str) -> bool:
    """Minimal CSS-selector matching against href/text of an <a> tag.

    Supported pseudo-patterns (enough for the config selectors we use):
        a[href$='.pdf']          -> href ends with .pdf
        a[href*='factsheet']     -> href contains factsheet (case-insensitive)
        Combinations with AND    -> a[href*='factsheet'][href$='.pdf']
    """
    # Extract all attribute conditions from selector.
    conditions = re.findall(r"\[([^\]]+)\]", selector)
    if not conditions:
        return False

    for cond in conditions:
        # Parse attr, operator, value.
        m = re.match(r"(\w+)([*$^~|]?=)['\"](.+?)['\"]", cond)
        if not m:
            continue
        attr, op, value = m.group(1), m.group(2), m.group(3)
        target = href if attr == "href" else text
        target_lower = target.lower()
        value_lower = value.lower()

        if op == "$=" and not target_lower.endswith(value_lower):
            return False
        if op == "*=" and value_lower not in target_lower:
            return False
        if op == "^=" and not target_lower.startswith(value_lower):
            return False

    return True


def find_pdf_links(html: str, amc_config: dict) -> list[str]:
    """Parse *html* to find PDF links relevant to the AMC's factsheet.

    Strategy:
    1. Try CSS selectors from amc_config, then fall back to default selectors.
    2. If no selector matches, do keyword search for links containing
       "factsheet", "monthly", and current month/year strings.
    3. Return de-duplicated list of hrefs.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        logger.warning("Failed to parse HTML for %s", amc_config.get("slug", "unknown"))
        return []

    all_links = parser.links

    # --- Phase 1: CSS selector matching ---
    selectors = amc_config.get("css_selectors", [])
    matched: list[str] = []

    for href, text in all_links:
        for sel in selectors:
            if _matches_css_like_selector(href, text, sel):
                matched.append(href)
                break

    if matched:
        return _dedupe(matched)

    # --- Phase 2: keyword / heuristic search ---
    now = datetime.now()
    keywords = list(amc_config.get("keywords", ["factsheet", "monthly"]))
    # Add temporal keywords for current and previous month.
    keywords.append(now.strftime("%B").lower())  # e.g. "march"
    keywords.append(now.strftime("%b").lower())   # e.g. "mar"
    keywords.append(str(now.year))                 # e.g. "2026"
    # Previous month too, since factsheets often lag.
    prev_month = (now.replace(day=1) - __import__("datetime").timedelta(days=1))
    keywords.append(prev_month.strftime("%B").lower())
    keywords.append(prev_month.strftime("%b").lower())

    for href, text in all_links:
        combined = (href + " " + text).lower()
        if not href.lower().endswith(".pdf"):
            continue
        if any(kw in combined for kw in keywords):
            matched.append(href)

    if matched:
        return _dedupe(matched)

    # --- Phase 3: grab any PDF link at all ---
    for href, _text in all_links:
        if href.lower().endswith(".pdf"):
            matched.append(href)

    if matched:
        logger.info(
            "No keyword-matched PDFs for %s; returning all %d PDF links found.",
            amc_config.get("slug", "unknown"),
            len(matched),
        )

    return _dedupe(matched)


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# Robots.txt (basic, cached per domain)
# ---------------------------------------------------------------------------

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


async def _check_robots(client: httpx.AsyncClient, url: str) -> bool:
    """Return True if we are allowed to fetch *url* per robots.txt.

    On any error (timeout, missing robots.txt, parse failure), we default to
    allowed so that the crawler does not silently skip legitimate pages.
    """
    parsed = urllib.parse.urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"

    if domain not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{domain}/robots.txt"
        try:
            resp = await client.get(robots_url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                # No robots.txt or error -> allow everything.
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        _robots_cache[domain] = rp

    rp = _robots_cache[domain]
    try:
        return rp.can_fetch("*", url)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Rate limiting helper
# ---------------------------------------------------------------------------

async def _rate_limit(domain: str, delay_min: float, delay_max: float) -> None:
    """Ensure a random delay between requests to the same domain."""
    if domain not in _domain_locks:
        _domain_locks[domain] = asyncio.Lock()

    async with _domain_locks[domain]:
        now = asyncio.get_event_loop().time()
        last = _domain_last_request.get(domain, 0.0)
        elapsed = now - last
        required = random.uniform(delay_min, delay_max)
        if elapsed < required:
            await asyncio.sleep(required - elapsed)
        _domain_last_request[domain] = asyncio.get_event_loop().time()


# ---------------------------------------------------------------------------
# Single HTTP request with retry + exponential backoff
# ---------------------------------------------------------------------------

async def _request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 5.0,
    timeout: float = 30.0,
    stream: bool = False,
) -> Optional[httpx.Response]:
    """GET *url* with retries on 429 / 5xx.  Returns None on total failure."""
    for attempt in range(1, max_retries + 1):
        try:
            headers = {"User-Agent": _random_ua()}
            if stream:
                # For large PDF downloads we stream to avoid holding the full
                # body in memory, but httpx context-manager streaming is
                # complex to expose here, so we just do a normal GET and rely
                # on the response content being buffered.
                resp = await client.get(
                    url, headers=headers, timeout=timeout, follow_redirects=True
                )
            else:
                resp = await client.get(
                    url, headers=headers, timeout=timeout, follow_redirects=True
                )

            if resp.status_code == 200:
                return resp

            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "HTTP %d for %s (attempt %d/%d). Retrying in %.1fs.",
                    resp.status_code,
                    url,
                    attempt,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            # Non-retryable error (e.g. 403, 404).
            logger.warning("HTTP %d for %s – not retrying.", resp.status_code, url)
            return None

        except (httpx.TimeoutException, httpx.NetworkError, httpx.TooManyRedirects) as exc:
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(
                "%s fetching %s (attempt %d/%d). Retrying in %.1fs.",
                type(exc).__name__,
                url,
                attempt,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)
        except Exception:
            logger.exception("Unexpected error fetching %s", url)
            return None

    logger.error("All %d attempts failed for %s", max_retries, url)
    return None


# ---------------------------------------------------------------------------
# Per-AMC fetch logic
# ---------------------------------------------------------------------------

async def fetch_factsheet_for_amc(
    client: httpx.AsyncClient,
    amc_config: dict,
    fetcher_config: dict,
    base_dir: str,
) -> DownloadResult:
    """Download the latest factsheet PDF for a single AMC.

    For consolidated factsheets (*type* == "consolidated"), we expect a single
    PDF covering all schemes.  We save it as
        ``{base_dir}/data/raw/{amc_slug}/{YYYY-MM-DD}.pdf``
    and skip if the file already exists (idempotent).
    """
    amc_slug = amc_config["slug"]
    amc_name = amc_config["name"]
    index_url = amc_config["factsheet_index_url"]
    date_str = date.today().isoformat()

    delay_min = fetcher_config.get("delay_between_requests_min", 2)
    delay_max = fetcher_config.get("delay_between_requests_max", 5)
    max_retries = fetcher_config.get("max_retries", 3)
    base_delay = fetcher_config.get("retry_delay_seconds", 5)
    timeout = fetcher_config.get("timeout_seconds", 30)

    # Destination path.
    dest_dir = Path(base_dir) / "data" / "raw" / amc_slug
    dest_path = dest_dir / f"{date_str}.pdf"

    # Idempotent: skip if already downloaded today.
    if dest_path.exists() and dest_path.stat().st_size > 0:
        logger.info("Already downloaded %s for %s – skipping.", dest_path, amc_name)
        return DownloadResult(
            path=str(dest_path),
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.SKIPPED,
        )

    # --- robots.txt check ---
    if not await _check_robots(client, index_url):
        logger.warning("Blocked by robots.txt: %s", index_url)
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error="Blocked by robots.txt",
        )

    # --- Fetch the index page ---
    domain = urllib.parse.urlparse(index_url).netloc
    await _rate_limit(domain, delay_min, delay_max)

    logger.info("Fetching index page for %s: %s", amc_name, index_url)
    resp = await _request_with_retry(
        client, index_url, max_retries=max_retries, base_delay=base_delay, timeout=timeout
    )
    if resp is None:
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error=f"Failed to fetch index page: {index_url}",
        )

    # --- Find PDF links ---
    pdf_links = find_pdf_links(resp.text, amc_config)
    if not pdf_links:
        logger.warning("No PDF links found for %s on %s", amc_name, index_url)
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error="No PDF links found on index page",
        )

    # Resolve relative URLs.
    pdf_url = urllib.parse.urljoin(index_url, pdf_links[0])
    logger.info("Found factsheet PDF for %s: %s", amc_name, pdf_url)

    # --- robots.txt check on PDF URL ---
    if not await _check_robots(client, pdf_url):
        logger.warning("Blocked by robots.txt: %s", pdf_url)
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error=f"Blocked by robots.txt: {pdf_url}",
        )

    # --- Download the PDF ---
    await _rate_limit(domain, delay_min, delay_max)

    logger.info("Downloading PDF for %s ...", amc_name)
    pdf_resp = await _request_with_retry(
        client,
        pdf_url,
        max_retries=max_retries,
        base_delay=base_delay,
        timeout=timeout,
        stream=True,
    )
    if pdf_resp is None:
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error=f"Failed to download PDF: {pdf_url}",
        )

    # Validate that response looks like a PDF.
    content = pdf_resp.content
    if not content or len(content) < 1024:
        logger.warning("PDF response too small (%d bytes) for %s", len(content), amc_name)
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error=f"PDF response too small ({len(content)} bytes)",
        )

    if not content[:5].startswith(b"%PDF-"):
        content_type = pdf_resp.headers.get("content-type", "")
        if "pdf" not in content_type.lower():
            logger.warning(
                "Response does not look like a PDF (content-type=%s) for %s",
                content_type,
                amc_name,
            )
            return DownloadResult(
                path=None,
                amc_slug=amc_slug,
                amc_name=amc_name,
                date_str=date_str,
                status=DownloadStatus.FAILED,
                error=f"Response is not a PDF (content-type={content_type})",
            )

    # --- Write to disk ---
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(content)
        logger.info(
            "Saved %s factsheet (%d bytes) to %s", amc_name, len(content), dest_path
        )
    except OSError as exc:
        logger.error("Failed to write %s: %s", dest_path, exc)
        return DownloadResult(
            path=None,
            amc_slug=amc_slug,
            amc_name=amc_name,
            date_str=date_str,
            status=DownloadStatus.FAILED,
            error=f"Disk write failed: {exc}",
        )

    return DownloadResult(
        path=str(dest_path),
        amc_slug=amc_slug,
        amc_name=amc_name,
        date_str=date_str,
        status=DownloadStatus.SUCCESS,
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

async def fetch_all_factsheets(config: dict) -> list[DownloadResult]:
    """Fetch factsheets for all configured AMCs concurrently.

    Parameters
    ----------
    config : dict
        Full configuration dict (loaded from YAML).

    Returns
    -------
    list[DownloadResult]
        One result per AMC, indicating success / failure / skipped.
    """
    fetcher_config = config.get("fetcher", {})
    amcs = config.get("amcs", [])
    concurrent_limit = fetcher_config.get("concurrent_downloads", 5)
    timeout = fetcher_config.get("timeout_seconds", 30)

    if not amcs:
        logger.warning("No AMCs configured – nothing to fetch.")
        return []

    # Determine base directory: the directory containing config.yaml, or cwd.
    base_dir = config.get("_base_dir", os.getcwd())

    semaphore = asyncio.Semaphore(concurrent_limit)

    # Shared httpx client with connection pooling.
    transport = httpx.AsyncHTTPTransport(
        retries=0,  # We handle retries ourselves.
        limits=httpx.Limits(
            max_connections=concurrent_limit * 2,
            max_keepalive_connections=concurrent_limit,
        ),
    )

    async def _bounded_fetch(
        client: httpx.AsyncClient, amc_config: dict
    ) -> DownloadResult:
        async with semaphore:
            try:
                return await fetch_factsheet_for_amc(
                    client, amc_config, fetcher_config, base_dir
                )
            except Exception as exc:
                logger.exception(
                    "Unhandled error fetching %s", amc_config.get("name", "unknown")
                )
                return DownloadResult(
                    path=None,
                    amc_slug=amc_config.get("slug", "unknown"),
                    amc_name=amc_config.get("name", "unknown"),
                    date_str=date.today().isoformat(),
                    status=DownloadStatus.FAILED,
                    error=str(exc),
                )

    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        tasks = [_bounded_fetch(client, amc) for amc in amcs]
        results = await asyncio.gather(*tasks)

    # Summary log.
    success = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
    failed = sum(1 for r in results if r.status == DownloadStatus.FAILED)
    skipped = sum(1 for r in results if r.status == DownloadStatus.SKIPPED)
    logger.info(
        "Fetch complete: %d success, %d failed, %d skipped out of %d AMCs.",
        success,
        failed,
        skipped,
        len(results),
    )

    return list(results)
