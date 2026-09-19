"""
Slack bot: replies with the daily summary when mentioned ("@bot give
summary"). Uses Socket Mode, so it works straight from a laptop with no
public URL or Render deployment needed -- good for a hackathon demo.

Needs SLACK_BOT_TOKEN (xoxb-...) and SLACK_APP_TOKEN (xapp-..., with the
connections:write scope) in .env.
"""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from core.neo4j_client import env
from core.summary import get_daily_summary, format_summary_message

# The replay demo lives in this fixed historical date -- change if the demo
# day changes. Real wall-clock "today" means nothing for replayed data.
DEMO_DATE = date(2021, 1, 23)  # matches the real severe-spike scenario in test_reroute.py

app = App(token=env("SLACK_BOT_TOKEN"))


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "").lower()
    if "summary" in text:
        summary = get_daily_summary(DEMO_DATE)
        say(format_summary_message(summary))
    else:
        say('Mention me with the word "summary" to get today\'s delivery + AQI report.')


if __name__ == "__main__":
    handler = SocketModeHandler(app, env("SLACK_APP_TOKEN"))
    print("Slack bot running (Socket Mode)... Ctrl+C to stop.")
    handler.start()
