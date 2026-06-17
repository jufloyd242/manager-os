# Workspace Activity from Google Chat Summary

Manager OS retrieves daily workspace activity summaries from a specific Google Chat space/app, **not** via a broad generic Google Workspace activity scan.

## Source of Truth

The configured Google Chat space contains a daily history of summarized workspace activity and already includes action items. Manager OS retrieves from this source, parses the daily summary, and treats its action items as high-priority inputs.

**Default URL:** `https://chat.google.com/u/0/app/chat/AAQA61WgdSs`

## Configuration

Add the following to your `.env` file:

```env
# Enable workspace retrieval
MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED=true
MANAGER_OS_RETRIEVE_WORKSPACE_ACTIVITY_WITH_GEMINI=true

# Activity source configuration
MANAGER_OS_WORKSPACE_ACTIVITY_SOURCE=google_chat_space
MANAGER_OS_WORKSPACE_ACTIVITY_CHAT_URL=https://chat.google.com/u/0/app/chat/AAQA61WgdSs
MANAGER_OS_WORKSPACE_ACTIVITY_LOOKBACK_DAYS=1
```

## Hard Rules

- **Read-only only.**
- Do not send messages.
- Do not reply in Chat.
- Do not edit/delete anything.
- Do not mutate Google Workspace.
- Do not dump raw Chat history into logs.
- Store only normalized local artifacts/snapshots.

## Action Item Priority

Action items extracted from this Chat summary are treated as **high-priority** and are ingested as first-class action items in the database.

Priority order for action items:
1. Workspace activity action items from Chat summary with `requires_attention=true`
2. Explicit `action_items[]` from the Chat summary
3. Explicit TODO/action items in current signal-tier notes
4. Waiting-on items
5. Follow-ups inferred by regex

The dashboard and daily brief will surface these prominently, showing:
- Description
- Assigned to
- Due date
- Entity (if available)
- **Source:** Google Chat activity summary
- Link (if available)

## Commands

### Fetch Activity
```bash
# Preview the prompt without contacting Gemini
manager-os workspace-fetch-activity --dry-run --print-prompt

# Fetch with a longer timeout
manager-os workspace-fetch-activity --timeout 300

# Override the Chat URL for a specific run
manager-os workspace-fetch-activity --chat-url "https://chat.google.com/u/0/app/chat/YOUR_URL"
```

### Ingest and Extract
```bash
# Ingest the fetched snapshot
manager-os ingest --source workspace

# Extract signals and action items (including the new high-priority Chat actions)
manager-os extract --mode both --llm-limit 25 --verbose
```

### Troubleshooting
If retrieval fails or returns no items:
1. Verify the URL is correct and accessible to the Gemini CLI service account.
2. Run `manager-os workspace-doctor` to check overall workspace retrieval configuration.
3. Check the snapshot directory: `data/raw/workspace_snapshots/activity/YYYY-MM-DD.json`

## Deduplication

Action items are deduplicated across reruns using a stable ID:
`workspace_activity::{summary_date}::{source_url}::{description}`

This ensures that running `manager-os ingest --source workspace` multiple times for the same day does not create duplicate action items.