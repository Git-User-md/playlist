import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

REQUEST_TIMEOUT = 8000  # 8 seconds
INITIAL_CONCURRENCY = 4
MAX_CONCURRENCY = 8
MIN_CONCURRENCY = 1
CONCURRENCY_STEP = 1
MAX_PASSES = 5  # fail-safe to avoid infinite loops

# ---------------------------
def get_domain(url):
    return urlparse(url).netloc

# ---------------------------
async def extract_m3u8(page, url, timeout_ms=REQUEST_TIMEOUT):
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
async def process_episode(sema, context, channel, show, episode, players, results, domain_cache):
    async with sema:
        page = await context.new_page()
        try:
            found = False
            ordered_players = list(players.items())

            # prioritize player used previously
            for i, (pname, url) in enumerate(ordered_players):
                if domain_cache.get(get_domain(url)) == pname:
                    ordered_players.insert(0, ordered_players.pop(i))
                    break

            for player_name, url in ordered_players:
                m3u8s = await extract_m3u8(page, url)
                if m3u8s:
                    r = m3u8s[0]
                    domain_cache[get_domain(url)] = player_name
                    results[channel][show][episode] = {
                        "m3u8_url": r.url,
                        "player_used": player_name,
                    }
                    found = True
                    break

            if not found:
                results[channel][show][episode] = None

        finally:
            if not page.is_closed():
                await page.close()

# ---------------------------
async def adaptive_runner(context, episodes_list, results, domain_cache):
    concurrency = INITIAL_CONCURRENCY
    pass_count = 0

    while episodes_list and pass_count < MAX_PASSES:
        sema = asyncio.Semaphore(concurrency)
        tasks = []

        for ch, sh, ep, players in episodes_list:
            tasks.append(process_episode(sema, context, ch, sh, ep, players, results, domain_cache))

        await asyncio.gather(*tasks)

        # collect failed episodes for next pass
        failed = []
        for ch, sh, ep, players in episodes_list:
            data = results[ch][sh][ep]
            if not data or "m3u8_url" not in data:
                failed.append((ch, sh, ep, players))

        # adjust concurrency
        success_count = len(episodes_list) - len(failed)
        if success_count == len(episodes_list) and concurrency < MAX_CONCURRENCY:
            concurrency += CONCURRENCY_STEP
        elif success_count < len(episodes_list) and concurrency > MIN_CONCURRENCY:
            concurrency -= CONCURRENCY_STEP

        episodes_list = failed
        pass_count += 1

# ---------------------------
async def main():
    if not PLAYER_JSON.exists():
        print("player_links.json not found")
        return

    with PLAYER_JSON.open("r", encoding="utf-8") as f:
        player_data = json.load(f)

    results = {}
    domain_cache = {}

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

        episodes_list = []
        for channel, shows in player_data.items():
            results.setdefault(channel, {})
            for show, episodes in shows.items():
                results[channel].setdefault(show, {})
                for episode, players in episodes.items():
                    episodes_list.append((channel, show, episode, players))

        await adaptive_runner(context, episodes_list, results, domain_cache)
        await browser.close()

    # ---------------- Save results
    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
