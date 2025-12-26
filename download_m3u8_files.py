import asyncio
import aiohttp
import aiofiles
import json
import logging
import re
import random
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("m3u8-dl")

# ---------------------------
# CONFIG
# ---------------------------
INPUT_JSON = Path("show_m3u8_links.json")
BASE_DIR = Path("m3u8_files")

REQUEST_TIMEOUT = 30
INITIAL_CONCURRENCY = 5
MAX_CONCURRENCY = 10
MIN_CONCURRENCY = 1
CONCURRENCY_STEP = 1
MAX_PASSES = 5

JITTER_MIN, JITTER_MAX = 0.2, 0.6
RETRIES = 3

# ---------------------------
def safe_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")

# ---------------------------
async def rewrite_m3u8(path: Path, host: str):
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        lines = (await f.read()).splitlines()

    out = []
    vod_added = False

    for line in lines:
        if not vod_added and line.startswith("#EXT-X-VERSION"):
            out.append(line)
            out.append("#EXT-X-PLAYLIST-TYPE:VOD")
            vod_added = True
            continue

        if line.startswith("/") and not line.startswith("//"):
            out.append(host + line)
        else:
            out.append(line)

    if not vod_added:
        out.insert(1, "#EXT-X-PLAYLIST-TYPE:VOD")

    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write("\n".join(out) + "\n")

# ---------------------------
async def download_m3u8(session, url, outpath: Path):
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": host,
        "Origin": host,
    }

    for attempt in range(1, RETRIES + 1):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    raise aiohttp.ClientError(f"HTTP {resp.status}")

                data = await resp.read()
                async with aiofiles.open(outpath, "wb") as f:
                    await f.write(data)

                return host

        except Exception as e:
            if attempt < RETRIES:
                await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            else:
                raise e

# ---------------------------
async def process_episode(sema, session, job, results):
    async with sema:
        channel, show, episode, info = job

        if not info or "m3u8_url" not in info:
            results[job] = False
            return

        folder = BASE_DIR / safe_name(channel) / safe_name(show)
        folder.mkdir(parents=True, exist_ok=True)

        outpath = folder / (safe_name(episode) + ".m3u8")

        try:
            await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            host = await download_m3u8(session, info["m3u8_url"], outpath)
            await rewrite_m3u8(outpath, host)
            results[job] = True
        except Exception:
            results[job] = False

# ---------------------------
async def adaptive_runner(jobs):
    concurrency = INITIAL_CONCURRENCY
    pending = jobs
    passes = 0

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit_per_host=concurrency)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        while pending and passes < MAX_PASSES:
            passes += 1
            total = len(pending)
            results = {}

            log.info("=" * 60)
            log.info(f"PASS {passes}")
            log.info(f"Concurrency: {concurrency}")
            log.info(f"Total episodes: {total}")

            sema = asyncio.Semaphore(concurrency)

            await asyncio.gather(*[
                process_episode(sema, session, job, results)
                for job in pending
            ])

            failed = [job for job, ok in results.items() if not ok]
            success = total - len(failed)

            log.info(f"Success: {success}")
            log.info(f"Failed: {len(failed)}")

            if success == total and concurrency < MAX_CONCURRENCY:
                concurrency += CONCURRENCY_STEP
                log.info(f"Increasing concurrency → {concurrency}")
            elif success < total and concurrency > MIN_CONCURRENCY:
                concurrency -= CONCURRENCY_STEP
                log.info(f"Decreasing concurrency → {concurrency}")

            pending = failed

        if pending:
            log.warning(f"Unresolved after {MAX_PASSES} passes: {len(pending)}")
        else:
            log.info("All episodes downloaded")

# ---------------------------
async def main():
    if not INPUT_JSON.exists():
        log.error("show_m3u8_links.json not found")
        return

    with INPUT_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = []
    for channel, shows in data.items():
        for show, episodes in shows.items():
            for episode, info in episodes.items():
                jobs.append((channel, show, episode, info))

    log.info(f"Total episodes queued: {len(jobs)}")
    await adaptive_runner(jobs)

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
