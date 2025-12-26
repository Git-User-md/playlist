import asyncio
import aiohttp
import aiofiles
import json
import re
import random
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------
# CONFIG
# ---------------------------
INPUT_JSON = Path("show_m3u8_links.json")
BASE_DIR = Path("m3u8_files")
MAX_CONCURRENT = 5   # adjust concurrency
JITTER_MIN, JITTER_MAX = 0.2, 0.6
RETRIES = 3
TIMEOUT = 30

# ---------------------------
# SAFE FILENAME
# ---------------------------
def safe_name(title):
    return re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_")

# ---------------------------
# REWRITE M3U8 FILE
# ---------------------------
async def rewrite_m3u8(path: Path, host: str):
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        lines = (await f.read()).splitlines()
    out = []
    vod_inserted = False

    for i, line in enumerate(lines):
        line = line.strip()
        if i == 0 and line == "#EXTM3U":
            out.append(line)
            continue
        if not vod_inserted and line.startswith("#EXT-X-VERSION"):
            out.append(line)
            out.append("#EXT-X-PLAYLIST-TYPE:VOD")
            vod_inserted = True
            continue
        if line.startswith("/") and not line.startswith("//"):
            out.append(host + line)
        else:
            out.append(line)

    if not vod_inserted:
        out.insert(1, "#EXT-X-PLAYLIST-TYPE:VOD")

    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write("\n".join(out) + "\n")

# ---------------------------
# DOWNLOAD SINGLE EPISODE
# ---------------------------
async def download_episode(session, url, outpath: Path):
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": host,
        "Origin": host,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(RETRIES):
        try:
            async with session.get(url, headers=headers, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    raise aiohttp.ClientError(f"Status {resp.status}")
                data = await resp.read()
                async with aiofiles.open(outpath, "wb") as f:
                    await f.write(data)
                return host
        except Exception as e:
            if attempt < RETRIES - 1:
                await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            else:
                raise e

# ---------------------------
# PROCESS SINGLE EPISODE
# ---------------------------
async def process_episode(session, channel, show, episode_title, info):
    if not info or "m3u8_url" not in info:
        print(f"✖ Skipping {episode_title} (no m3u8)")
        return

    m3u8_url = info["m3u8_url"]
    filename = safe_name(episode_title) + ".m3u8"
    folder = BASE_DIR / safe_name(channel) / safe_name(show)
    folder.mkdir(parents=True, exist_ok=True)
    outpath = folder / filename

    await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))  # jitter
    try:
        print(f"⬇️ {episode_title}")
        host = await download_episode(session, m3u8_url, outpath)
        await rewrite_m3u8(outpath, host)
        print(f"✅ Saved → {outpath}")
    except Exception as e:
        print(f"✖ Failed {episode_title}: {e}")

# ---------------------------
# MAIN
# ---------------------------
async def main():
    if not INPUT_JSON.exists():
        print(f"⚠️ {INPUT_JSON} not found")
        return

    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = []
        for channel, shows in data.items():
            for show, episodes in shows.items():
                for episode_title, info in episodes.items():
                    tasks.append(
                        process_episode(session, channel, show, episode_title, info)
                    )

        # Limit concurrency
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def sem_task(task):
            async with sem:
                await task

        await asyncio.gather(*(sem_task(t) for t in tasks))

if __name__ == "__main__":
    asyncio.run(main())
