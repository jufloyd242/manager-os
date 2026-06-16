# Data Quality Fixes (2026-06-16)

This document summarizes the data-quality and dashboard fixes made in the
`Fix dashboard data quality and deal staffing context issues` commit.

---

## Calendar: Solo/No-Attendee Events Are Ignored

**During ingestion** (`gws_client._ingest_calendar_file`):
- Events with no attendees are skipped. `skip_reasons["no_external_attendees"]` is
  incremented.
- Events where **all** attendees have `"self": true` are also skipped (personal
  timeblocks, lunch, focus time).
- Events with at least one non-self attendee are ingested normally.

**During dashboard query** (`dashboard_data.get_meetings_for_date`):
- As a second line of defense, meetings with empty `attendees` arrays are excluded
  from the Meeting Prep dropdown, even if they were previously ingested.

**Logging**: skipped count is logged at DEBUG level. Raw event content is never logged.

---

## Meeting Prep: Dict/Object Mismatch Fixed

**Root cause**: `generate_meeting_prep` expects a `MeetingRecord` Pydantic object, but
`get_meetings_for_date` returns plain dicts. Accessing `.linked_entities` on a dict
raised `AttributeError`.

**Fix**:
- `get_meetings_for_date` continues returning `list[dict]` (stable public contract).
- A new helper `meeting_dict_to_record(m: dict) -> MeetingRecord` converts a dict to
  a `MeetingRecord` safely, with `linked_entities` and `attendees` defaulting to `[]`
  if missing.
- The dashboard Meeting Prep tab now calls `meeting_dict_to_record(chosen)` before
  passing to `generate_meeting_prep`.
- All dict access in the dashboard remains consistent via `m["key"]`; all
  `generate_meeting_prep` call sites use object attribute access.

---

## Meeting Prep: Duplicate Meetings Deduplicated

`get_meetings_for_date` now deduplicates meetings using a deterministic key:
- **Primary key**: `external_id` (Google Calendar event ID)
- **Fallback key**: `(normalized_title, start_time, meeting_date)`

When duplicates exist, the **richest** record is kept:
1. Has attendees > 0
2. Has `external_id`
3. Has more `linked_entities`
4. Has `source`

---

## Staffing Summary: Allocation Category Definitions

The Forecast tab now shows three categories, correctly classifying 100% allocation:

| Category | Condition |
|---|---|
| **Overallocated** | Person has ANY week > 100.01% |
| **Fully Utilized** | All of person's weeks are within [99.99%, 100.01%] |
| **Available** | Person has ANY week < 99.99% and NO overallocated week |

**Important**: Exactly 100% is **Fully Utilized**, not Available. This was the prior bug.

**Per-person-week classification first** — a person at 100% for 4 weeks in a 30-day
window stays in Fully Utilized, not 400% overallocated.

### Window Labels

Window labels now include real date ranges:
- `2w (2026-06-16 → 2026-06-30)`
- `30d (2026-06-16 → 2026-07-16)`
- `60d (2026-06-16 → 2026-08-15)`

---

## Deal SOW and Deal Sheet Link Retrieval

### New module: `src/manager_os/ingest/drive_deal_docs.py`

Searches Google Drive via Gemini CLI (read-only) for:
- **INT SOW** (`document_type = "int_sow"`)
- **Deal Sheet** (`document_type = "deal_sheet"`)

Results are stored in the new `deal_documents` DuckDB table:

```
deal_documents:
  id, deal_id, account, deal_name, document_type,
  title, url, source, retrieved_at, search_status, error
```

### New CLI command: `manager-os workspace-fetch-deal-docs`

```
manager-os workspace-fetch-deal-docs [OPTIONS]

  --date YYYY-MM-DD      Date label for snapshot (default: today)
  --deal-id TEXT         Fetch docs for a single deal only
  --limit INTEGER        Max deals to process
  --timeout INTEGER      Per-call timeout in seconds (default: 60)
  --dry-run              Print prompt without contacting Drive
  --print-prompt         Show the Gemini prompt
  --force                Re-fetch even if results exist
```

### Dashboard display

The Deals tab shows clickable links for each deal when available:
- `📄 [INT SOW](url) — Title`
- `📊 [Deal Sheet](url) — Title`

If no links are found: `📄 INT SOW: not found — run workspace-fetch-deal-docs`

---

## Staffing Feasibility Provenance

The Deals tab now shows the **source** of `staffing_feasibility` as a metric label:
- `Staffing (deals_csv)` — value came from the ingested deals CSV
- `Staffing (unknown)` — value was not set; defaulted to `feasible`

This makes it explicit that the field is not computed from matching.
Future phases will add `computed` provenance from forecast + skill matching.

---

## Clients Page: Opportunity Numbers

The Clients tab now shows an **Active opportunities** table inside each client expander:

| Opp # | Deal | Stage | Close |
|---|---|---|---|
| OPP-001 | Big Project | SOW Review | 2026-07-01 |

Data is sourced from the `deals` table joined by account name.

---

## People: Alias Normalization and Ignore List

### Config changes

`config/people.yaml` now supports a `track` field:

```yaml
- name: "Taylor Stacey"
  aliases: ["Taylor", "taylor", "T. Stacey"]
  role: "Staff AI Engineer"
  track: true   # false = hide from dashboard

- name: "Some Person"
  aliases: ["Some Alias"]
  track: false  # excluded from People tab and signals
```

### New module: `src/manager_os/build/people_normalization.py`

`PeopleNormalizer` provides:
- `canonicalize(name)` → canonical name (or unchanged if unknown)
- `is_tracked(name)` → bool
- `tracked_names()` → sorted list of canonical tracked names
- `find_unconfigured(names)` → names not in config at all

Used in:
- `get_people_rows` — canonical name grouping + track filter
- `get_people_rows` — forecast allocation lookup by canonical name
- Signal and 1:1 note lookups canonicalize before grouping

### New CLI command: `manager-os people-audit`

```
manager-os people-audit [--verbose]
```

Output:
- Configured tracked people
- People with `track=false`
- Duplicate candidates (raw names in DB that resolve to different canonical names)
- Unconfigured names seen in notes/forecast/signals but not in `people.yaml`
- Full alias map (with `--verbose`)

### Effect on People dashboard

- **"Taylor"** in notes or forecast → canonicalized to **"Taylor Stacey"** before display
- People with `track: false` do not appear in the People tab
- Duplicate rows for aliases of the same person are merged
