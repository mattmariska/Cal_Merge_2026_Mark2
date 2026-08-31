"""
merge_calendars.py

This script:
1. Downloads two .ics (iCalendar) subscription calendars.
2. Parses each one and removes any calendar event that is likely to cause
   problems downstream:
     - Events that have no start date at all.
     - Events dated more than 100 years in the future.
   Bad events are simply dropped, not repaired.
3. Combines the remaining "clean" events from both calendars into one
   merged .ics file.
4. Saves that merged file locally so the GitHub Actions workflow can then
   upload it to your FTP server.
"""

import os
import requests
from icalendar import Calendar
from datetime import datetime, timezone, date

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# The two source calendars you want to merge together.
SOURCE_CALENDARS = [
    "https://calendar.playmetrics.com/calendars/c2906/t575090/p0/tA19D6042/f/calendar.ics",
    "https://calendar.sportsyou.com/access/us-0a2307fa-b4ff-4ed6-a5a4-db176f4edfd0/c53e182b-ca32-4591-b9a8-98fa348b0d68",
]

# Where the merged file goes. It lives in an "output" folder so the GitHub
# Actions workflow can upload just this folder to FTP without touching
# the rest of the repo (the .py/.txt/.yml files).
OUTPUT_DIR = "output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "merged.ics")

# Any event whose start date is more than this many years in the future
# (compared to right now) gets dropped. Some calendar feeds contain
# corrupted/placeholder dates far in the future that break other calendar
# apps, so we filter those out.
MAX_YEARS_IN_FUTURE = 100


def fetch_calendar(url):
    """
    Downloads a .ics file from a URL and parses it into an icalendar
    Calendar object. Returns None if the download or the initial parse
    fails completely (individual bad *events* are handled separately below —
    this only catches a totally broken/unreachable feed).
    """
    print(f"Downloading calendar: {url}")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()  # Raises an error on a bad HTTP status (e.g. 404)
    except requests.RequestException as e:
        print(f"  -> Could not download this calendar: {e}")
        return None

    try:
        return Calendar.from_ical(response.text)
    except Exception as e:
        print(f"  -> Could not parse this calendar as valid iCalendar data: {e}")
        return None


def event_start_as_datetime(component):
    """
    Pulls the start date/time out of a single event (VEVENT) and normalizes
    it into a timezone-aware datetime so it can be safely compared to other
    dates. Handles both "whole day" events (e.g. 2026-05-04) and events with
    a specific time attached.

    Returns None if there's no usable start date at all.
    """
    dtstart_prop = component.get("dtstart")
    if dtstart_prop is None:
        return None

    value = dtstart_prop.dt  # Either a datetime.date or a datetime.datetime

    # Whole-day event (no time attached) -> treat as midnight UTC.
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    # "Naive" datetime with no timezone attached -> assume UTC so comparisons
    # below don't crash.
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value


def is_event_safe_to_keep(component):
    """
    Decides whether one event should be kept. Returns True to keep it,
    False to drop it.

    Dropped if:
      - It has no start date at all, OR
      - Its start date is more than MAX_YEARS_IN_FUTURE years from now, OR
      - Anything unexpected goes wrong while reading it (better to drop one
        odd event than let it break the whole merged calendar).
    """
    try:
        start = event_start_as_datetime(component)

        if start is None:
            print(f"  -> Dropping event with no start date: {component.get('summary', 'Untitled event')}")
            return False

        now = datetime.now(timezone.utc)
        years_in_future = (start - now).days / 365.25

        if years_in_future > MAX_YEARS_IN_FUTURE:
            print(f"  -> Dropping event dated too far in the future ({start}): {component.get('summary', 'Untitled event')}")
            return False

        return True

    except Exception as e:
        print(f"  -> Dropping event due to unexpected error while reading it: {e}")
        return False


def main():
    # Make sure the output folder exists.
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # This is the calendar object that will hold our final merged result.
    merged_calendar = Calendar()
    merged_calendar.add("prodid", "-//Merged Calendar//mergecalendars.py//EN")
    merged_calendar.add("version", "2.0")
    merged_calendar.add("x-wr-calname", "Merged Calendar")
    # Hints to calendar apps that support it to refresh roughly hourly.
    merged_calendar.add("refresh-interval;value=duration", "PT1H")

    total_kept = 0
    total_dropped = 0

    for url in SOURCE_CALENDARS:
        source_calendar = fetch_calendar(url)

        if source_calendar is None:
            # Skip this whole source if it couldn't be downloaded/parsed at
            # all, but keep going so the other calendar still gets merged.
            print("  -> Skipping this entire calendar due to the error above.\n")
            continue

        # walk("VEVENT") finds every individual event in the calendar.
        for component in source_calendar.walk("VEVENT"):
            if is_event_safe_to_keep(component):
                merged_calendar.add_component(component)
                total_kept += 1
            else:
                total_dropped += 1

    print(f"\nDone. Kept {total_kept} events, dropped {total_dropped} problematic events.")

    with open(OUTPUT_FILE, "wb") as f:
        f.write(merged_calendar.to_ical())

    print(f"Merged calendar saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
