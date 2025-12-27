from pathlib import Path
from datetime import datetime, timedelta
import re

BASE_DIR = Path("m3u8_files")
KEEP_DAYS = 8

# ---------------------------
# DATE PARSER FROM FILENAME
# ---------------------------
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12
}

DATE_REGEX = re.compile(
    r"(\d{1,2})(st|nd|rd|th)_([A-Za-z]+)_(\d{4})",
    re.IGNORECASE
)

def extract_date(filename: str):
    match = DATE_REGEX.search(filename)
    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(3).lower()
    year = int(match.group(4))

    month = MONTHS.get(month_name)
    if not month:
        return None

    return datetime(year, month, day)

# ---------------------------
# CLEANUP
# ---------------------------
def main():
    cutoff = datetime.utcnow() - timedelta(days=KEEP_DAYS)

    if not BASE_DIR.exists():
        return

    for path in BASE_DIR.rglob("*.m3u8"):
        file_date = extract_date(path.name)

        if not file_date:
            continue

        if file_date < cutoff:
            try:
                path.unlink()
                print(f"🗑 Deleted: {path}")
            except Exception as e:
                print(f"✖ Failed delete {path}: {e}")

if __name__ == "__main__":
    main()
