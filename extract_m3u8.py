import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

PLAYER_JSON = Path("player_links.json")
OUTPUT_JSON = Path("show_m3u8_links.json")

FAST_TIMEOUT = 2.0
SLOW_TIMEOUT = 8.0

PAGE_POOL_SIZE = 4
SEM_LIMIT = 4


# ---------------------------
# DOMAIN HELPER
# ---------------------------
def get_domain(url):
    return urlparse(url).netloc


# ---------------------------
# BROWSER LAUNCH
# ---------------------------
async def launch():
    p = await async_playwright().start()

    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-sync",
            "--disable-default-apps",
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

    async def route_filter(route, request):
        if request.resource_type in {"image", "font", "stylesheet"}:
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", route_filter)

    return p, browser, context


# ---------------------------
# PAGE POOL
# ---------------------------
async def create_page_pool(context, size):
    pages = []
    for _ in range(size):
        page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64' });
        """)
        pages.append(page)
    return asyncio.Queue(maxsize=size), pages


# ---------------------------
# M3U8 EXTRACTOR
# ---------------------------
async def extract_m3u8(page, url):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def on_request(request):
        if request.url.lower().endswith(".m3u8") and not future.done():
            future.set_result(request.url)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        try:
            return await asyncio.wait_for(future, FAST_TIMEOUT)
        except asyncio.TimeoutError:
            return await asyncio.wait_for(future, SLOW_TIMEOUT - FAST_TIMEOUT)

    except asyncio.TimeoutError:
        return None
    finally:
        page.remove_listener("request", on_request)


# ---------------------------
# EPISODE WORKER
# ---------------------------
async def process_episode(
    sem,
    page_queue,
    channel,
    show,
    episode,
    players,
    results,
    domain_cache,
):
    async with sem:
        page = await page_queue.get()

        try:
            ordered = list(players.items())

            for i, (pname, url) in enumerate(ordered):
                if domain_cache.get(get_domain(url)) == pname:
                    ordered.insert(0, ordered.pop(i))
                    break

            for player_name, url in ordered:
                print(f"  trying: {player_name}")
                m3u8 = await extract_m3u8(page, url)

                if m3u8:
                    domain_cache[get_domain(url)] = player_name
                    results[channel][show][episode] = {
                        "m3u8_url": m3u8,
                        "player_used": player_name,
                    }
                    print("  ✔ m3u8 found")
                    return

            results[channel][show][episode] = None
            print("  ✖ no m3u8 found")

        finally:
            page_queue.put_nowait(page)


# ---------------------------
# MAIN
# ---------------------------
async def main():
    if not PLAYER_JSON.exists():
        return

    with PLAYER_JSON.open("r", encoding="utf-8") as f:
        player_data = json.load(f)

    p = browser = context = None
    results = {}
    domain_cache = {}
    sem = asyncio.Semaphore(SEM_LIMIT)

    try:
        p, browser, context = await launch()
        page_queue, pages = await create_page_pool(context, PAGE_POOL_SIZE)

        for page in pages:
            page_queue.put_nowait(page)

        tasks = []

        for channel, shows in player_data.items():
            results.setdefault(channel, {})

            for show, episodes in shows.items():
                results[channel].setdefault(show, {})

                for episode, players in episodes.items():
                    print(f"\n▶ {episode}")
                    task = asyncio.create_task(
                        process_episode(
                            sem,
                            page_queue,
                            channel,
                            show,
                            episode,
                            players,
                            results,
                            domain_cache,
                        )
                    )
                    tasks.append(task)

        await asyncio.gather(*tasks)

        with OUTPUT_JSON.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    finally:
        if browser:
            await browser.close()
        if p:
            await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
