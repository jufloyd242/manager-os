# Project document fetch — which command to use

## Use `project-docs-fetch`

`manager-os project-docs-fetch` is the primary, trusted command for fetching
project-memory documents (SOW, deal sheet, project plan, architecture,
runbook, proposal/LOE, etc.) from Google Drive via Gemini CLI.

- `--opportunity-number OPP...` — fetch docs for one known project (resolved
  by normalized OppID against the `projects` table).
- `--batch --limit-projects N` — bounded batch discovery across up to `N`
  projects in a single Gemini CLI call, amortizing prompt boilerplate instead
  of paying for it once per project.
- `--dry-run` — preview what would happen; never calls Gemini/Workspace.
- `--print-prompt` — print the exact prompt that would be sent; never calls
  Gemini/Workspace.

Data is written to the **`project_documents`** table.

### Safe command sequence (single project)

```
manager-os project-docs-fetch --opportunity-number OPP031267 --dry-run
manager-os project-docs-fetch --opportunity-number OPP031267 --print-prompt
manager-os project-docs-fetch --opportunity-number OPP031267 --limit 3 --timeout 60 --verbose
```

### Safe command sequence (batch)

```
manager-os project-docs-fetch --batch --dry-run --limit-projects 5
manager-os project-docs-fetch --batch --print-prompt --limit-projects 5
manager-os project-docs-fetch --batch --limit-projects 5 --limit 3 --timeout 120 --verbose
```

Always run `--dry-run` and `--print-prompt` first to build trust before a
live call.

## Do not use `workspace-fetch-deal-docs` for project OppID lookups

`manager-os workspace-fetch-deal-docs` is a separate, legacy, **deal-ID**
based command. It writes to the **`deal_documents`** table (not
`project_documents`) and its underlying `deals` table has no
`opportunity_number` column.

Passing `--opportunity-number` to `workspace-fetch-deal-docs` now exits
non-zero with guidance to use `project-docs-fetch` instead — it no longer
silently treats the OppID as a raw `--deal-id` (which could previously match
zero rows and report success with zero work done).

`workspace-fetch-deal-docs --deal-id <id>` still works unchanged for legacy
deal-specific document lookups.
