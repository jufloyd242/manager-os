# Manager OS Dashboard Development Checklist

Use **Terminal → Run Task…** in VS Code to run numbered validation tasks.

## Quick Start

```bash
./manager-os start
```

Or use **Terminal → Run Task… → Manager OS: Start Dashboard**.

## Environment Setup

- [ ] `./manager-os doctor` — diagnose setup
- [ ] `./manager-os build` — build frontend
- [ ] `cp .env.example .env` and edit paths (if not using defaults)

## Backend Tests

- [ ] **Task 01**: Backend Compile (`python -m compileall src/manager_os`)
- [ ] **Task 02**: Backend Targeted Tests (deals, forecast, workspace, meetings)
- [ ] **Task 03**: Backend Full Tests (`pytest tests/ -q`)
- [ ] **Task 04**: Backend Ruff (`ruff check src/`)
- [ ] **Task 05**: Backend Pyright (`pyright src/`)

## Frontend Tests

- [ ] **Task 06**: Frontend Lint (`npm run lint`)
- [ ] **Task 07**: Frontend Tests (`npm run test`)
- [ ] **Task 08**: Frontend Build (`npm run build`)

## Full Validation

- [ ] **Task 09**: Full Validation (runs all checks sequentially)

## Local Refresh Verification

- [ ] Start API: **Task 10** (`python -m uvicorn ...`)
- [ ] Verify `GET /api/health` returns 200
- [ ] Verify `GET /api/status` returns valid JSON
- [ ] Verify `GET /api/daily` returns daily loop
- [ ] Verify `GET /api/deals` returns deal data
- [ ] Verify `GET /api/forecast` returns forecast data
- [ ] Verify `GET /api/meetings?date=<today>` returns meetings
- [ ] Verify `GET /api/workspace-context?date=<today>&lookback_days=7` returns context

## Calendar Sync Safety

- [ ] `GET /api/meetings?date=...` does NOT invoke Calendar API
- [ ] Only explicit `POST /api/meetings/sync-calendar` triggers sync

## Deals Verification

- [ ] Deals API returns complete field set
- [ ] Attention levels are populated (critical/high/medium/low)
- [ ] Search and filters work
- [ ] Refresh from configured file works via `POST /api/refresh`

## Forecast Verification

- [ ] Forecast API returns allocation data
- [ ] Week navigation selects correct periods
- [ ] Overallocation and underutilization are classified
- [ ] Roll-off detection works

## Workspace Context Verification

- [ ] Context returns items for a date with lookback
- [ ] Entity linking shows method and evidence
- [ ] Deduplication works

## Meeting Prep Verification

- [ ] Meetings appear for selected date
- [ ] Prep endpoint returns resolved attendees with relationships
- [ ] Rule matching works (direct report, manager, client, team, no-prep)
- [ ] Direct-report prep includes recent 1:1s, commitments, staffing
- [ ] Client prep includes workspace context
- [ ] No external calls during deterministic prep

## Frontend Verification

- [ ] Start React: **Task 11** (`npm run dev`)
- [ ] Today view shows summary cards capped at 5 actions
- [ ] Deals view loads and searches
- [ ] Forecast view shows allocation with week navigation
- [ ] Meetings view allows date selection
- [ ] Sync button is explicit (no automatic sync)
- [ ] Empty/failure states show honest messages, not fake data
- [ ] Project-document data does not dominate views

## Git Review

- [ ] `git status --short` is clean (no unintended files)
- [ ] `git diff --check` has no whitespace errors
- [ ] Only intended files are staged

## Commit

- [ ] Commit message: `feat: deliver core manager dashboard workflows`
- [ ] Body includes summary of changes

## Push

- [ ] `git push` succeeds
- [ ] Branch pushed to remote