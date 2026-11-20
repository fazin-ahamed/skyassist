# Retired Streamlit UI

The first passenger kiosk and coordinator dashboard were Streamlit apps. They were replaced by the BoardUI
React front end in `web/`, which the API service serves. This copy is kept for reference only and is not part of
the default test run or the launcher.

Run its tests, if you need them: `PYTHONPATH=legacy/streamlit uv run --group legacy pytest legacy/streamlit/tests`.
