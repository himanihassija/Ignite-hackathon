"""
Posts today's (the demo's) summary to Slack right now. Use this for the
scheduled EOD post -- or just run it manually during the demo to show the
Slack integration off on cue.
"""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.summary import get_daily_summary, format_summary_message
from core.slack_notify import post_slack_message

DEMO_DATE = date(2021, 1, 23)  # matches the real severe-spike scenario in test_reroute.py

summary = get_daily_summary(DEMO_DATE)
message = format_summary_message(summary)
print(message)
print()
post_slack_message(message)
print("Posted to Slack.")
