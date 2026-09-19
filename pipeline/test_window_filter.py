"""
Pure date-math test for core/scoring.py's passes_window_filter() -- no Neo4j,
no API calls, so this runs anywhere Python + the project are available.
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.scoring import passes_window_filter

WINDOW_START = datetime(2021, 1, 15, 10, 0, 0)
WINDOW_END = datetime(2021, 1, 15, 10, 45, 0)

cases = [
    # (description, depart_ts, duration_s, expected)
    ("Depart at window start, 20 min route -> arrives 10:20, inside window", WINDOW_START, 20 * 60, True),
    ("Depart at window start, 0-second route -> arrives exactly at start (boundary)", WINDOW_START, 0, True),
    ("Depart at window start, 45 min route -> arrives exactly at end (boundary)", WINDOW_START, 45 * 60, True),
    ("Depart at window start, 46 min route -> arrives 1 min past end", WINDOW_START, 46 * 60, False),
    ("Depart 10 min before window start, 5 min route -> arrives before window opens", WINDOW_START - timedelta(minutes=10), 5 * 60, False),
    ("Depart 30 min before window start, 40 min route -> arrives 10:10, inside window", WINDOW_START - timedelta(minutes=30), 40 * 60, True),
]

all_passed = True
for desc, depart_ts, duration_s, expected in cases:
    actual = passes_window_filter(duration_s, WINDOW_START, WINDOW_END, depart_ts)
    status = "PASS" if actual == expected else "FAIL"
    if actual != expected:
        all_passed = False
    print(f"[{status}] {desc}  (expected={expected}, got={actual})")

print()
print("ALL_PASSED" if all_passed else "SOME_FAILED")
