import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Error as PlaywrightError

PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

REQUEST_TIMEOUT = 8000
INITIAL_CONCURRENCY = 4
MAX_CONCURRENCY = 8
MIN_CONCURRENCY = 1
CONCURRENCY_STEP = 1
MAX_PASSES = 5

# ---------------------------
def get_domain(url: str) -> str:
    return urlparse(url).netloc

# ---------------------------
async def extract_m3u8(page, url):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def on_request(request):
        if request.url.endswith(".m3u8") and not future.done():
            future.set_result(request.url)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT)
        return await asyncio.wait_for(future, REQUEST_TIMEOUT / 1000)
    except asyncio.TimeoutError:
        return None
    finally:
        page.remove_listener("request", on_request)

# ---------------------------
async def process_episode(
    sema,
    context,
    channel,
    show,
    episode,
    players,
    results,
    domain_cache: dict,
):
    async with sema:
        page = await context.new_page()
        try:
            ordered = list(players.items())

            for i, (pname, url) in enumerate(ordered):
                if domain_cache.get(get_domain(url)) == pname:
                    ordered.insert(0, ordered.pop(i))
                    break

            for player_name, url in ordered:
                m3u8 = await extract_m3u8(page, url)
                if m3u8:
                    domain_cache[get_domain(url)] = player_name
                    results[channel][show][episode] = {
                        "m3u8_url": m3u8,
                        "player_used": player_name,
                    }
                    return

            results[channel][show][episode] = None

        except PlaywrightError:
            results[channel][show][episode] = None
        finally:
            await page.close()

# ---------------------------
async def adaptive_runner(context, episodes, results, domain_cache):
    concurrency = INITIAL_CONCURRENCY

    for _ in range(MAX_PASSES):
        if not episodes:
            break

        sema = asyncio.Semaphore(concurrency)

        await asyncio.gather(
            *[
                process_episode(
                    sema, context, ch, sh, ep, players, results, domain_cache
                )
                for ch, sh, ep, players in episodes
            ],
            return_exceptions=True,
        )

        failed = [
            (ch, sh, ep, players)
            for ch, sh, ep, players in episodes
            if not results[ch][sh].get(ep)
        ]

        success = len(episodes) - len(failed)

        if success == len(episodes):
            concurrency = min(MAX_CONCURRENCY, concurrency + CONCURRENCY_STEP)
        else:
            concurrency = max(MIN_CONCURRENCY, concurrency - CONCURRENCY_STEP)

        episodes = failed

# ---------------------------
async def main():
    with PLAYER_JSON.open() as f:
        data = json.load(f)

    results = {}
    domain_cache = {}  # MUST stay dict

    episodes = []
    for ch, shows in data.items():
        results[ch] = {}
        for sh, eps in shows.items():
            results[ch][sh] = {}
            for ep, players in eps.items():
                episodes.append((ch, sh, ep, players))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        await adaptive_runner(context, episodes, results, domain_cache)

        await browser.close()

    OUTPUT_JSON.write_text(json.dumps(results, indent=2))

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
