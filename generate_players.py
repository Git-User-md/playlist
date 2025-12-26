# ---------------------------
# fast_episode_players_requests_json.py
# ---------------------------
import json
import requests
from lxml import html
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------
# CONFIG & CONSTANTS
# ---------------------------
BASE_DIR = Path.cwd()
CONFIG_FILE = BASE_DIR / "config" / "shows.json"
KEEP_DAYS = 7
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ---------------------------
# PARSE EPISODE DATE FROM TITLE
# ---------------------------
def parse_episode_date(title):
    import re, calendar

    title_lower = title.lower()
    if "preview" in title_lower or "promo" in title_lower:
        return None

    match = re.search(r"(\d{1,2})(?:st|nd|rd|th)? (\w+) (\d{4})", title, re.IGNORECASE)
    if not match:
        return None

    day, month_str, year = match.groups()
    month_str_cap = month_str.capitalize()

    try:
        month = list(calendar.month_name).index(month_str_cap)
    except ValueError:
        try:
            month = list(calendar.month_abbr).index(month_str_cap)
        except ValueError:
            return None

    return datetime(int(year), month, int(day), tzinfo=timezone.utc).date()

# ---------------------------
# GET EPISODES FROM SHOW PAGE
# ---------------------------
def get_episode_links(show_url):
    today = datetime.now(timezone.utc).date()
    try:
        resp = requests.get(show_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        tree = html.fromstring(resp.content)
        eps = tree.xpath("//div[contains(@class,'layout_post_1')]//h4/a")
        results = []
        for e in eps:
            title = e.text_content().strip()
            href = e.get("href")
            ep_date = parse_episode_date(title)
            if ep_date and (today - ep_date).days <= KEEP_DAYS:
                results.append({"title": title, "url": href})
        return results
    except Exception as ex:
        print(f"⚠️ Failed to fetch episodes from {show_url}: {ex}")
        return []

# ---------------------------
# GET PLAYERS FROM EPISODE PAGE
# ---------------------------
def get_players(episode_url):
    players = {}
    try:
        resp = requests.get(episode_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        tree = html.fromstring(resp.content)
        paragraphs = tree.xpath("//p")

        for i, p in enumerate(paragraphs):
            b = p.xpath(".//b/span")
            if not b:
                continue
            text = b[0].text_content().strip().lower()
            if "watch online" not in text:
                continue

            if i + 1 < len(paragraphs):
                next_p = paragraphs[i + 1]
                a = next_p.xpath(".//a")
                if a:
                    link = a[0].get("href")
                    players[text] = link
    except Exception as e:
        print(f"        ⚠️ Player fetch failed: {e}")
    return players

# ---------------------------
# MAIN
# ---------------------------
def main():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"⚠️ {CONFIG_FILE} not found!")
        return

    output = {}

    for channel_key, channel_data in config.items():
        print(f"\n📺 CHANNEL: {channel_key}")
        output[channel_key] = {}

        for slug in channel_data.get("shows", []):
            show_url = f"https://www.desitellybox.to/category/{channel_key}/{slug}/"
            print(f"\n  ▶ SHOW: {slug}")
            print(f"      🔗 {show_url}")

            episodes = get_episode_links(show_url)
            if not episodes:
                print("    ⚠️ No episodes found for last 7 days.")
                continue

            output[channel_key][slug] = {}

            for ep in episodes:
                print(f"\n    ▸ {ep['title']}")
                print(f"        🔗 {ep['url']}")
                players = get_players(ep["url"])
                output[channel_key][slug][ep['title']] = players

    # Save all player links in structured JSON
    with open("player_links.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4)
    print("\n✅ Saved all player links to player_links.json")

# ---------------------------
# SAFE RUN
# ---------------------------
if __name__ == "__main__":
    main()
