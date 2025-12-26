import subprocess
import re
from pathlib import Path
from urllib.parse import urlparse
import json
import time, random
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------
# INPUT FILE
# ---------------------------
INPUT_JSON = Path("show_m3u8_links.json")
BASE_DIR = Path("m3u8_files")
MAX_WORKERS = 1  # safer default

# ---------------------------
# SAFE FILENAME
# ---------------------------
def safe_name(title):
    return re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_")

# ---------------------------
# DOWNLOAD M3U8 USING CURL
# ---------------------------
def download_m3u8(url, outfile):
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"

    cmd = [
        "curl",
        "--http1.1",
        "-L",
        url,
        "-o",
        str(outfile),
        "-H", "accept: */*",
        "-H", "accept-language: en-US,en;q=0.9",
        "-H", f"origin: {host}",
        "-H", f"referer: {host}",
        "-H",
        "user-agent: Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return host

# ---------------------------
# FIX RELATIVE PATHS AND ADD VOD
# ---------------------------
def rewrite_m3u8(path, host):
    lines = path.read_text().splitlines()
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

    path.write_text("\n".join(out) + "\n")

# ---------------------------
# PROCESS SINGLE EPISODE
# ---------------------------
def process_episode(channel, show, episode_title, info):
    if not info or "m3u8_url" not in info:
        print(f"✖ Skipping {episode_title} (no m3u8)")
        return

    m3u8_url = info["m3u8_url"]
    filename = safe_name(episode_title) + ".m3u8"

    folder = BASE_DIR / safe_name(channel) / safe_name(show)
    folder.mkdir(parents=True, exist_ok=True)
    outpath = folder / filename

    # ---- JITTER HERE ----
    time.sleep(random.uniform(0.2, 0.6))
    # --------------------

    try:
        print(f"⬇️ {episode_title}")
        host = download_m3u8(m3u8_url, outpath)
        rewrite_m3u8(outpath, host)
        print(f"✅ Saved → {outpath}")
    except Exception as e:
        print(f"✖ Failed {episode_title}: {e}")

# ---------------------------
# MAIN
# ---------------------------
def main():
    if not INPUT_JSON.exists():
        print(f"⚠️ {INPUT_JSON} not found")
        return

    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    tasks = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for channel, shows in data.items():
            for show, episodes in shows.items():
                for episode_title, info in episodes.items():
                    tasks.append(
                        executor.submit(
                            process_episode,
                            channel,
                            show,
                            episode_title,
                            info
                        )
                    )

        for future in as_completed(tasks):
            try:
                future.result()
            except Exception as e:
                print(f"✖ Worker error: {e}")

if __name__ == "__main__":
    main()
