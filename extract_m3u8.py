import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

# ---------------------------
# TIMEOUTS
# ---------------------------
FAST_TIMEOUT = 2000
SLOW_TIMEOUT = 8000

# ---------------------------
# HARDENED BROWSER LAUNCH
# ---------------------------
async def launch():
    p = await async_playwright().start()

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

    async def strip(route, request):
        headers = dict(request.headers)
        for h in list(headers):
            if h.lower().startswith("sec-ch-ua"):
                headers.pop(h, None)
        await route.continue_(headers=headers)

    await context.route("**/*", strip)

    page = await context.new_page()

    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64' });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    """)

    return p, browser, page

# ---------------------------
# M3U8 EXTRACTOR
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
        page.remove_listener("request", on_request)

# ---------------------------
# DOMAIN HELPER
# ---------------------------
def get_domain(url):
    return urlparse(url).netloc

# ---------------------------
# MAIN LOGIC
# ---------------------------
async def main():
    if not PLAYER_JSON.exists():
        print(f"{PLAYER_JSON} not found")
        return

    with PLAYER_JSON.open("r", encoding="utf-8") as f:
        player_data = json.load(f)

    p = browser = page = None
    results = {}
    domain_cache = {}

    try:
        p, browser, page = await launch()

        for channel, shows in player_data.items():
            results.setdefault(channel, {})

            for show, episodes in shows.items():
                results[channel].setdefault(show, {})

                for episode, players in episodes.items():
                    print(f"\n▶ {episode}")
                    found = False

                    ordered_players = list(players.items())
                    for i, (pname, url) in enumerate(ordered_players):
                        d = get_domain(url)
                        if domain_cache.get(d) == pname:
                            ordered_players.insert(0, ordered_players.pop(i))
                            break

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

                    if not found:
                        results[channel][show][episode] = None
                        print("  ✖ no m3u8 found")

        with OUTPUT_JSON.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    finally:
        if browser:
            await browser.close()
        if p:
            await p.stop()

# ---------------------------
# ENTRY POINT
# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
