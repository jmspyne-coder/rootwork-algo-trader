"""Pytest config: keep unit tests hermetic and OFF the real broker / database.

Cleared before any test module imports config.settings (which runs load_dotenv
with override=False, so these blanks win over .env). can_trade() persists a halt
via MotherDuck; with a blank token that write fails closed and is swallowed, so
no test ever touches my_db or Alpaca.
"""
import os

os.environ["MOTHERDUCK_TOKEN"] = ""
os.environ["ALPACA_API_KEY_ID"] = ""
os.environ["ALPACA_API_SECRET_KEY"] = ""
os.environ["SLACK_WEBHOOK_URL"] = ""
os.environ["GMAIL_ADDRESS"] = ""
os.environ["GMAIL_APP_PASSWORD"] = ""
