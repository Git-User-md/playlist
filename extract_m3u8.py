import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError

# ---------------------------
# LOGGING SETUP
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("m3u8")

# ---------------------------
PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

REQUEST_TIMEOUT = 8000  # 8 seconds
INITIAL_CONCURRENCY = 4
MAX_CONCURRENCY = 8
MIN_CONCURRENCY = 1
CONCURRENCY_STEP = 1
MAX_PASSES = 5


# ---------------------------
def get_domain(url: str) -> str:
    return urlparse(url).netloc


# ---------------------------
async def extract_m3u8(page, url: str):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def on_request(request):
        if request.url.endswith(".m3u8") and not future.done():
            log.info(f"M3U8 detected → {request.url}")
            future.set_result(request.url)

    page.on("request", on_request)

    try:
        log.info(f"Opening player: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # allow player to mount
        await page.wait_for_timeout(400)

        # center click to trigger player
        vp = page.viewport_size
        if vp:
            await page.mouse.click(vp["width"] // 2, vp["height"] // 2)
            log.info("Center click executed")

        return await asyncio.wait_for(future, REQUEST_TIMEOUT / 1000)

    except (asyncio.TimeoutError, TimeoutError):
        log.warning("Timeout waiting for m3u8")
        return None

    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass


# ---------------------------
async def process_episode(
    sema,
    context,
    channel,
    show,
    episode,
    players,
    results,
    domain_cache,
):
    async with sema:
        page = await context.new_page()
        log.info(f"START → {channel} | {show} | {episode}")

        try:
            ordered = list(players.items())

            # prioritize known-good domain
            for i, (pname, url) in enumerate(ordered):
                if domain_cache.get(get_domain(url)) == pname:
                    ordered.insert(0, ordered.pop(i))
                    log.info(f"Prioritized cached player: {pname}")
                    break

            for player_name, url in ordered:
                log.info(f"Trying player: {player_name}")
                m3u8 = await extract_m3u8(page, url)

                if m3u8:
                    domain_cache[get_domain(url)] = player_name
                    results[channel][show][episode] = {
                        "m3u8_url": m3u8,
                        "player_used": player_name,
                    }
                    log.info(f"SUCCESS → {episode} via {player_name}")
                    return

            log.warning(f"FAILED → {episode}")
            results[channel][show][episode] = None

        finally:
            await page.close()


# ---------------------------
async def adaptive_runner(context, episodes, results, domain_cache):
    concurrency = INITIAL_CONCURRENCY
    passes = 0
    pending = episodes

    while pending and passes < MAX_PASSES:
        passes += 1
        total = len(pending)

        log.info("=" * 60)
        log.info(f"PASS {passes}")
        log.info(f"Concurrency: {concurrency}")
        log.info(f"Total episodes: {total}")

        sema = asyncio.Semaphore(concurrency)

        await asyncio.gather(*[
            process_episode(
                sema,
                context,
                ch,
                sh,
                ep,
                players,
                results,
                domain_cache,
            )
            for ch, sh, ep, players in pending
        ])

        failed = []
        for ch, sh, ep, players in pending:
            data = results[ch][sh].get(ep)
            if not data or "m3u8_url" not in data:
                failed.append((ch, sh, ep, players))

        success = total - len(failed)

        log.info(f"Success: {success}")
        log.info(f"Failed: {len(failed)}")

        # adaptive concurrency
        if success == total and concurrency < MAX_CONCURRENCY:
            concurrency += CONCURRENCY_STEP
            log.info(f"Increasing concurrency → {concurrency}")
        elif success < total and concurrency > MIN_CONCURRENCY:
            concurrency -= CONCURRENCY_STEP
            log.info(f"Decreasing concurrency → {concurrency}")

        pending = failed

    if pending:
        log.warning(f"Unresolved episodes after {MAX_PASSES} passes: {len(pending)}")
    else:
        log.info("All episodes resolved")


# ---------------------------
async def main():
    if not PLAYER_JSON.exists():
        log.error("player_links.json not found")
        return

    with PLAYER_JSON.open("r", encoding="utf-8") as f:
        player_data = json.load(f)

    results = {}
    domain_cache = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        episodes = []
        for channel, shows in player_data.items():
            results.setdefault(channel, {})
            for show, eps in shows.items():
                results[channel].setdefault(show, {})
                for ep, players in eps.items():
                    episodes.append((channel, show, ep, players))

        log.info(f"Total episodes loaded: {len(episodes)}")

        await adaptive_runner(context, episodes, results, domain_cache)
        await browser.close()

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    log.info("Results written to show_m3u8_links.json")


# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
