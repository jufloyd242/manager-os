"""FastAPI read-only API for Manager OS.

Exposes local DuckDB-backed data (daily operating loop, people, meetings,
projects, feedback) over HTTP for a future React/Tailwind command tower.

Local-first, read-only: no writes, no live Gemini/Workspace/Drive/Calendar/
Chat/Sheets calls are made by anything in this package.
"""
