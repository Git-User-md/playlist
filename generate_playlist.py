import re
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------
# CONFIG
# ---------------------------
BASE_DIR = Path("m3u8_files")
OUTPUT_M3U = Path("playlist.m3u")

GITHUB_USER = "Git-User-md"
GITHUB_REPO = "playlist"
GITHUB_BRANCH = "main"

DAYS_LIMIT = 8

# ---------------------------
# DATE PARSER
# ---------------------------
DATE_RE = re.compile(
    r"(\d{1,2})(st|nd|rd|th)_([A-Za-z]+)_(\d{4})"
)

def extract_date(name: str):
    m = DATE_RE.search(name)
    if not m:
        return None

    day, _, month, year = m.groups()
    try:
        return datetime.strptime(
            f"{day} {month} {year}",
            "%d %B %Y"
        )
    except ValueError:
        return None

# ---------------------------
# RAW GITHUB URL
# ---------------------------
def raw_url(path: Path):
    rel = path.as_posix()
    return (
        f"https://raw.githubusercontent.com/"
        f"{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{rel}"
    )

# ---------------------------
# MAIN
# ---------------------------
def main():
    cutoff = datetime.utcnow() - timedelta(days=DAYS_LIMIT)

    entries = []

    for m3u8 in BASE_DIR.rglob("*.m3u8"):
        date = extract_date(m3u8.name)
        if not date or date < cutoff:
            continue

        rel_parts = m3u8.relative_to(BASE_DIR).parts
        if len(rel_parts) < 3:
            continue

        channel = rel_parts[0]
        show = rel_parts[2]
        group_title = f"{channel}/{show}"

        title = m3u8.stem.replace("_", " ")

        entries.append({
            "group": group_title,
            "title": title,
            "url": raw_url(m3u8)
        })

    entries.sort(key=lambda x: x["title"], reverse=True)

    with OUTPUT_M3U.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n\n")
        for e in entries:
            f.write(
                f'#EXTINF:-1 group-title="{e["group"]}",'
                f'{e["title"]}\n'
            )
            f.write(e["url"] + "\n\n")

    print(f"Playlist generated: {OUTPUT_M3U}")
    print(f"Entries: {len(entries)}")

# ---------------------------
if __name__ == "__main__":
    main()
