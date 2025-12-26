import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Error as PlaywrightError

PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

# ---------------------------
# TUNING
# ---------------------------
FAST_TIMEOUT = 2
SLOW_TIMEOUT = 8
MAX_CONCURRENCY = 4  # safe for GitHub Actions

# ---------------------------
def get_domain(url):
    return urlparse(url).netloc

# ---------------------------
async def extract_m3u8(page, url, timeout):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def on_request(req):
        if req.url.lower().endswith(".m3u8") and not future.done():
            future.set_result(req.url)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return await asyncio.wait_for(future, timeout)
    except (asyncio.TimeoutError, PlaywrightError):
        return None
    finally:
        if not page.is_closed():
            page.remove_listener("request", on_request)

# ---------------------------
async def process_episode(
    sema,
    context,
    channel,
    show,
    episode,
    players,
    domain_cache,
    results,
):
    async with sema:
        page = await context.new_page()
        try:
            ordered_players = list(players.items())

            # domain cache priority
            for i, (pname, url) in enumerate(ordered_players):
                if domain_cache.get(get_domain(url)) == pname:
                    ordered_players.insert(0, ordered_players.pop(i))
                    break

            for pname, url in ordered_players:
                print(f"  trying: {pname} (fast)")
                m3u8 = await extract_m3u8(page, url, FAST_TIMEOUT)

                if not m3u8:
                    print(f"  retrying: {pname} (slow)")
                    m3u8 = await extract_m3u8(page, url, SLOW_TIMEOUT)

                if m3u8:
                    domain_cache[get_domain(url)] = pname
                    results[channel][show][episode] = {
                        "m3u8_url": m3u8,
                        "player_used": pname,
                    }
                    print("  ✔ m3u8 found")
                    return

            results[channel][show][episode] = None
            print("  ✖ no m3u8 found")

        finally:
            if not page.is_closed():
                await page.close()

# ---------------------------
async def main():
    if not PLAYER_JSON.exists():
        print("player_links.json not found")
        return

    with PLAYER_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)

    results = {}
    domain_cache = {}
    sema = asyncio.Semaphore(MAX_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--mute-audio",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1366, "height": 768},
        )

        tasks = []

        for channel, shows in data.items():
            results.setdefault(channel, {})
            for show, episodes in shows.items():
                results[channel].setdefault(show, {})
                for episode, players in episodes.items():
                    print(f"\n▶ {episode}")
                    tasks.append(
                        process_episode(
                            sema,
                            context,
                            channel,
                            show,
                            episode,
                            players,
                            domain_cache,
                            results,
                        )
                    )

        await asyncio.gather(*tasks)
        await browser.close()

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
