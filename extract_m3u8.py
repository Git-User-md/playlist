import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

FAST_TIMEOUT = 2000
SLOW_TIMEOUT = 8000
MAX_CONCURRENCY = 4  # safe for GitHub Actions

# ---------------------------
def get_domain(url):
    return urlparse(url).netloc

# ---------------------------
async def extract_m3u8(page, url, timeout_ms):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def on_request(request):
        if request.url.lower().endswith(".m3u8") and not future.done():
            future.set_result(request)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        req = await asyncio.wait_for(future, timeout_ms / 1000)
        return [req]
    except asyncio.TimeoutError:
        return []
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
    results,
    domain_cache,
):
    async with sema:
        page = await context.new_page()
        try:
            print(f"\n▶ {episode}")
            found = False

            ordered_players = list(players.items())
            # Prioritize last successful player for this domain
            for i, (pname, url) in enumerate(ordered_players):
                if domain_cache.get(get_domain(url)) == pname:
                    ordered_players.insert(0, ordered_players.pop(i))
                    break

            # Keep retrying until a valid m3u8 is found
            while not found:
                for player_name, url in ordered_players:
                    print(f"  trying: {player_name} (fast)")
                    m3u8s = await extract_m3u8(page, url, FAST_TIMEOUT)

                    if not m3u8s:
                        print(f"  retrying: {player_name} (slow)")
                        m3u8s = await extract_m3u8(page, url, SLOW_TIMEOUT)

                    if m3u8s:
                        r = m3u8s[0]
                        domain_cache[get_domain(url)] = player_name
                        results[channel][show][episode] = {
                            "m3u8_url": r.url,
                            "player_used": player_name,
                        }
                        print("  ✔ m3u8 found")
                        found = True
                        break

        finally:
            if not page.is_closed():
                await page.close()

# ---------------------------
async def main():
    if not PLAYER_JSON.exists():
        print("player_links.json not found")
        return

    with PLAYER_JSON.open("r", encoding="utf-8") as f:
        player_data = json.load(f)

    results = {}
    domain_cache = {}
    sema = asyncio.Semaphore(MAX_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=UserAgentClientHint",
                "--no-sandbox",
                "--disable-dev-shm-usage",
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

        for channel, shows in player_data.items():
            results.setdefault(channel, {})
            for show, episodes in shows.items():
                results[channel].setdefault(show, {})
                for episode, players in episodes.items():
                    tasks.append(
                        process_episode(
                            sema,
                            context,
                            channel,
                            show,
                            episode,
                            players,
                            results,
                            domain_cache,
                        )
                    )

        await asyncio.gather(*tasks)
        await browser.close()

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
