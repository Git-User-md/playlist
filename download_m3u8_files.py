import asyncio
import aiohttp
import aiofiles
import json
import re
import random
from pathlib import Path
from urllib.parse import urlparse
import logging

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("m3u8_downloader")

# ---------------------------
# CONFIG
# ---------------------------
INPUT_JSON = Path("show_m3u8_links.json")
BASE_DIR = Path("m3u8_files")
MAX_CONCURRENT = 5
JITTER_MIN, JITTER_MAX = 0.2, 0.6
RETRIES = 3
TIMEOUT = 30
INITIAL_CONCURRENCY = 8
MAX_CONCURRENCY = 10
MIN_CONCURRENCY = 1
CONCURRENCY_STEP = 1
MAX_PASSES = 5

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
# DOWNLOAD SINGLE M3U8
# ---------------------------
async def download_m3u8(session, url, outpath: Path):
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
async def process_episode(sema, session, job, results):
    async with sema:
        channel, show, episode_title, info = job
        key = (channel, show, episode_title)

        if not info or "m3u8_url" not in info:
            log.warning(f"✖ Skipping {episode_title} (no m3u8)")
            results[key] = False
            return

        folder = BASE_DIR / safe_name(channel) / safe_name(show)
        folder.mkdir(parents=True, exist_ok=True)
        outpath = folder / (safe_name(episode_title) + ".m3u8")

        await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
        try:
            log.info(f"⬇️ Downloading {episode_title}")
            host = await download_m3u8(session, info["m3u8_url"], outpath)
            await rewrite_m3u8(outpath, host)
            log.info(f"✅ Saved → {outpath}")
            results[key] = True
        except Exception as e:
            log.warning(f"✖ Failed {episode_title}: {e}")
            results[key] = False

# ---------------------------
# ADAPTIVE RUNNER
# ---------------------------
async def adaptive_runner(jobs):
    concurrency = INITIAL_CONCURRENCY
    passes = 0
    pending = jobs
    results = {}

    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        while pending and passes < MAX_PASSES:
            passes += 1
            total = len(pending)
            log.info("=" * 60)
            log.info(f"PASS {passes}")
            log.info(f"Concurrency: {concurrency}")
            log.info(f"Total episodes: {total}")

            sem = asyncio.Semaphore(concurrency)
            await asyncio.gather(*(process_episode(sem, session, job, results) for job in pending))

            failed = [job for job in pending if not results[(job[0], job[1], job[2])]]
            success = total - len(failed)

            log.info(f"Success: {success}")
            log.info(f"Failed: {len(failed)}")

            # Adaptive concurrency
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
# MAIN
# ---------------------------
async def main():
    if not INPUT_JSON.exists():
        log.error(f"{INPUT_JSON} not found")
        return

    with INPUT_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = []
    for channel, shows in data.items():
        for show, episodes in shows.items():
            for ep, info in episodes.items():
                jobs.append((channel, show, ep, info))

    log.info(f"Total episodes queued: {len(jobs)}")
    await adaptive_runner(jobs)

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
