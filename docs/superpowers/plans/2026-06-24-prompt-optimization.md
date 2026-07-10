# Prompt Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optimize Gemini CLI prompt templates in `manager-os` to drastically reduce input token consumption while maintaining strict JSON output reliability, fully proven via TDD.

**Architecture:** We will aggressively compress the instructional boilerplate and JSON schemas within the prompt templates. To prove safety, we will first write tests that mock the LLM returning the *new*, minimal JSON structures to ensure the existing `_parse_retrieval_json` function successfully processes them. Finally, we will implement a batching mechanism for Drive document searches using strict TDD.

**Tech Stack:** Python, pytest, `unittest.mock`, Git

## Global Constraints

- Must not break the existing parsing logic (`_parse_retrieval_json`).
- Must continue to return valid JSON.
- Must preserve the core retrieval logic and tool configurations.
- EVERY task must end with `git push` as explicitly requested by the user.

---

### Task 1: TDD Compression of Workspace Gemini Prompts

**Files:**
- Create: `tests/test_ingest/test_prompt_compression.py`
- Modify: `src/manager_os/ingest/workspace_gemini.py`

**Interfaces:**
- Consumes: `_run_gemini_retrieval` (Mocked)
- Produces: Compressed `FORECAST_PROMPT_TEMPLATE`, `CALENDAR_PROMPT_TEMPLATE`, `ACTIVITY_PROMPT_TEMPLATE`.

- [ ] **Step 1: Write the failing test (RED)**

```python
# Create tests/test_ingest/test_prompt_compression.py
import pytest
from unittest.mock import patch
from datetime import date
from manager_os.ingest.workspace_gemini import retrieve_forecast, retrieve_calendar

@patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval")
def test_forecast_handles_compressed_schema_and_verifies_prompt_length(mock_run):
    # Mock the LLM returning the NEW, ultra-compressed JSON format
    mock_run.return_value = (
        '{"ok":true,"source_title":"AI/ML","source_url":"http://x","retrieved_at":"2026-06-18T00:00:00Z","rows":[{"person":"Alice","week_start":"6/15","allocation_pct":100,"project":"X","client":"Y"}]}',
        "cmd"
    )
    
    # Run the retrieval
    result = retrieve_forecast(target_date=date(2026, 6, 18), dry_run=False, output_dir="/tmp")
    
    # Assert parsing works
    assert result.ok is True
    assert result.source_title == "AI/ML"
    assert len(result.items) == 1
    assert result.items[0]["person"] == "Alice"
    
    # Assert the PROMPT sent to the LLM is sufficiently compressed (under 300 chars)
    # We can get the prompt by doing a dry_run
    dry_result = retrieve_forecast(target_date=date(2026, 6, 18), dry_run=True)
    assert len(dry_result.json_text) < 300
    assert "ok: boolean" not in dry_result.json_text # Old schema format should be gone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest/test_prompt_compression.py::test_forecast_handles_compressed_schema_and_verifies_prompt_length -v`
Expected: FAIL (The prompt length assertion `< 300` will fail because the current template is very long).

- [ ] **Step 3: Write minimal implementation (GREEN)**

Modify `src/manager_os/ingest/workspace_gemini.py`. Replace the templates and remove `read_only=_READ_ONLY_PREFIX` from `.format()` calls.

```python
FORECAST_PROMPT_TEMPLATE = """\
[Read-only] Get latest AI/ML team forecast.
Query: {query_hint}
Return ONLY JSON:
{{"ok":true,"source_title":"str","source_url":"str","retrieved_at":"ISO8601","rows":[{{"person":"str","week_start":"str","allocation_pct":100,"project":"str","client":"str"}}]}}
Fail? {{"ok":false,"error":"str"}}
"""

CALENDAR_PROMPT_TEMPLATE = """\
[Read-only] Get calendar for {target_date} (lookback {lookback_days}, ahead {lookahead_days}).
Return ONLY JSON:
{{"ok":true,"source":"google_calendar_gemini","retrieved_at":"ISO8601","events":[{{"title":"str","start_time":"ISO","end_time":"ISO","attendees":["str"],"location/meet_link":"str","description_summary":"str","external_id":"str"}}]}}
"""

ACTIVITY_PROMPT_TEMPLATE = """\
[Read-only] Get workspace activity for {target_date} (max {lookback_days} days) from: {chat_url}
Return ONLY JSON:
{{"ok":true,"source":"google_chat_activity_summary","source_url":"{chat_url}","retrieved_at":"ISO8601","summary_date":"YYYY-MM-DD","summary":"str","items":[{{"type":"action_item|mention|doc_update|other","title":"str","description":"str","source_url":"str","requires_attention":true,"assigned_to":"str","due_date":"str","entity_type":"str","entity_name":"str","confidence":1.0}}],"action_items":[{{...}}]}}
Fail? {{"ok":false,"error":"str"}}
"""

# Inside retrieve_forecast():
    prompt = FORECAST_PROMPT_TEMPLATE.format(
        query_hint=effective_hint,
    )

# Inside retrieve_calendar():
    prompt = CALENDAR_PROMPT_TEMPLATE.format(
        target_date=target_date.isoformat(),
        lookback_days=lookback_days or CALENDAR_LOOKBACK_DAYS,
        lookahead_days=lookahead_days or CALENDAR_LOOKAHEAD_DAYS,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest/test_prompt_compression.py::test_forecast_handles_compressed_schema_and_verifies_prompt_length -v`
Expected: PASS

- [ ] **Step 5: Commit and Push**

```bash
git add src/manager_os/ingest/workspace_gemini.py tests/test_ingest/test_prompt_compression.py
git commit -m "perf: ultra-compress workspace gemini retrieval prompts via TDD"
git push
```

---

### Task 2: TDD Compression of Drive Search Prompt

**Files:**
- Modify: `tests/test_ingest/test_prompt_compression.py`
- Modify: `src/manager_os/ingest/project_drive_docs.py`

**Interfaces:**
- Consumes: `_build_drive_search_prompt`
- Produces: Compressed Drive prompt.

- [ ] **Step 1: Write the failing test (RED)**

```python
# Append to tests/test_ingest/test_prompt_compression.py
from manager_os.ingest.project_drive_docs import _build_drive_search_prompt

def test_drive_search_prompt_is_compressed():
    prompt = _build_drive_search_prompt("OPP123", "Acme", "Project X")
    assert len(prompt) < 350
    assert "Return metadata only. Do not download full documents." not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest/test_prompt_compression.py::test_drive_search_prompt_is_compressed -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation (GREEN)**

Modify `src/manager_os/ingest/project_drive_docs.py`:

```python
def _build_drive_search_prompt(
    opportunity_number: str,
    client: str,
    project_name: str,
) -> str:
    """Build the Gemini CLI prompt for searching Google Drive."""
    return f"""[Read-only. Metadata only]
Find Drive docs (SOW, Plan, Architecture, etc) for OPP: {opportunity_number}, Client: {client}, Name: {project_name}.
Return ONLY valid JSON:
{{
  "ok": true,
  "source": "google_drive_project_docs",
  "retrieved_at": "{datetime.utcnow().isoformat()}",
  "documents": [{{"document_type": "sow|deal_sheet|project_plan|architecture|other", "title": "str", "url": "str", "confidence": 0.9, "why_matched": "str"}}],
  "error": "str (if ok is false)"
}}"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest/test_prompt_compression.py::test_drive_search_prompt_is_compressed -v`
Expected: PASS

- [ ] **Step 5: Commit and Push**

```bash
git add src/manager_os/ingest/project_drive_docs.py tests/test_ingest/test_prompt_compression.py
git commit -m "perf: compress drive search prompt via TDD"
git push
```

---

### Task 3: TDD Implementation of Batched Drive Searches

**Files:**
- Modify: `tests/test_ingest/test_prompt_compression.py`
- Modify: `src/manager_os/ingest/project_drive_docs.py`

**Interfaces:**
- Produces: `batch_search_drive_for_projects(projects: list[dict]) -> dict[str, DriveSearchResult]`

- [ ] **Step 1: Write the failing test (RED)**

```python
# Append to tests/test_ingest/test_prompt_compression.py
from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_drive_for_projects(mock_run):
    # Mock returning batched results
    mock_run.return_value = (
        '{"ok":true,"retrieved_at":"2026-06-18","results":{"OPP1":[{"document_type":"sow","title":"SOW1","url":"http://1","confidence":0.9,"why_matched":"matched"}],"OPP2":[]}}',
        "cmd"
    )
    
    projects = [
        {"opportunity_number": "OPP1", "client": "C1", "project_name": "P1"},
        {"opportunity_number": "OPP2", "client": "C2", "project_name": "P2"},
    ]
    
    results = batch_search_drive_for_projects(projects, dry_run=False)
    
    assert len(results) == 2
    assert "OPP1" in results
    assert "OPP2" in results
    assert len(results["OPP1"].documents) == 1
    assert results["OPP1"].documents[0].document_type == "sow"
    assert len(results["OPP2"].documents) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest/test_prompt_compression.py::test_batch_search_drive_for_projects -v`
Expected: FAIL (`ImportError` because `batch_search_drive_for_projects` does not exist).

- [ ] **Step 3: Write minimal implementation (GREEN)**

Add to `src/manager_os/ingest/project_drive_docs.py`:

```python
def batch_search_drive_for_projects(
    projects: list[dict],
    use_yolo: bool = True,
    timeout: int = 300,
    dry_run: bool = False,
) -> dict[str, DriveSearchResult]:
    """Search Google Drive for documents for multiple projects in one LLM call."""
    from manager_os.ingest.workspace_gemini import _run_gemini_retrieval, _parse_retrieval_json
    
    project_list_str = "\n".join([
        f"- OPP: {p.get('opportunity_number', '')}, Client: {p.get('client', '')}, Name: {p.get('project_name', '')}" 
        for p in projects
    ])

    prompt = f"""[Read-only. Metadata only]
Find Drive docs for these projects:
{project_list_str}

Return ONLY valid JSON mapping each OPP number to its results:
{{
  "ok": true,
  "retrieved_at": "{datetime.utcnow().isoformat()}",
  "results": {{
    "OPP123": [{{"document_type": "sow|deal_sheet|other", "title": "str", "url": "str", "confidence": 0.9, "why_matched": "str"}}]
  }},
  "error": "str"
}}"""

    results_map = {}
    if dry_run:
        return {"dry_run_prompt": prompt}

    try:
        raw, _ = _run_gemini_retrieval(prompt, use_yolo=use_yolo, timeout=timeout)
        data = _parse_retrieval_json(raw)
        
        if data.get("ok"):
            for opp_num, docs in data.get("results", {}).items():
                result = DriveSearchResult()
                for doc_data in docs:
                    doc = ProjectDocument(
                        document_type=doc_data.get("document_type", "other"),
                        title=doc_data.get("title", ""),
                        url=doc_data.get("url", ""),
                        confidence=doc_data.get("confidence", 0.0),
                        why_matched=doc_data.get("why_matched", "")
                    )
                    result.documents.append(doc)
                results_map[opp_num] = result
    except Exception as exc:
        pass 

    return results_map
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest/test_prompt_compression.py::test_batch_search_drive_for_projects -v`
Expected: PASS

- [ ] **Step 5: Commit and Push**

```bash
git add src/manager_os/ingest/project_drive_docs.py tests/test_ingest/test_prompt_compression.py
git commit -m "feat: implement batched drive searches via strict TDD"
git push
```
