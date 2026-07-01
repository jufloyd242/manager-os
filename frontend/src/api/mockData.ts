import type {
  StatusCardData,
  DailyOperatingLoop,
  CommandSpec,
  RunRecord,
  ValidateResponse,
  RunResponse,
  RunLogs,
} from './client'

export const mockSystemStatus: StatusCardData[] = [
  {
    id: 'obsidian',
    label: 'Obsidian Vault',
    detail: 'Last ingested 2h ago',
    freshness: 'fresh',
    count: 214,
  },
  {
    id: 'forecast',
    label: 'Staffing Forecast',
    detail: 'Last ingested 1 day ago',
    freshness: 'stale',
    count: 42,
  },
  {
    id: 'deals',
    label: 'Deal Pipeline',
    detail: 'Last ingested 4h ago',
    freshness: 'fresh',
    count: 18,
  },
  {
    id: 'workspace',
    label: 'Workspace Summary',
    detail: 'No snapshot found for today',
    freshness: 'missing',
  },
]

// Mirrors the exact shape of Python's build_daily_operating_loop() dict.
export const mockDailyOperatingLoop: DailyOperatingLoop = {
  date: '2026-06-30',
  people_staffing: [
    { person: 'Jordan Lee', signal: 'No 1:1 in 18 days', severity: 'medium' },
    { person: 'Priya Nair', signal: 'Overallocated 128% next 2 weeks', severity: 'high' },
  ],
  meetings: [
    { title: '1:1 with Jordan Lee', time: '10:00', needs_prep: true },
    { title: 'Acme Corp Steering Committee', time: '14:00', needs_prep: true },
  ],
  projects_deals: [
    { name: 'Acme Corp — Phase 2', risk: 'SOW review due in 3 days', severity: 'high' },
    { name: 'Globex — Renewal', risk: 'Deal stalled 21 days', severity: 'medium' },
  ],
  document_gaps: [
    { project: 'Acme Corp — Phase 2', missing: 'Closure deck' },
    { project: 'Initech — Discovery', missing: 'SOW' },
  ],
  feedback_learning: [{ note: '3 open feedback candidates awaiting review' }],
  recommended_actions: [
    {
      title: 'Review Acme Corp SOW before Friday deadline',
      reason: 'SOW deadline in 3 days with no reviewer assigned',
      command: 'manager-os project-docs-fetch --opportunity-number OPP-ACME-002',
      priority: 'high',
    },
    {
      title: 'Schedule 1:1 with Jordan Lee',
      reason: 'No 1:1 recorded in 18 days',
      command: 'manager-os meeting-prep --meeting jordan-lee-1-1',
      priority: 'medium',
    },
    {
      title: 'Investigate Priya Nair overallocation',
      reason: '128% allocation over next 2 weeks',
      command: 'manager-os extract --entity person',
      priority: 'high',
    },
    {
      title: 'Follow up on Globex renewal',
      reason: 'Deal stalled with no activity in 21 days',
      command: 'manager-os brief --date 2026-06-30',
      priority: 'low',
    },
  ],
  warnings: [],
}

export const mockCommandRegistry: CommandSpec[] = [
  {
    command_id: 'status',
    label: 'Status',
    description: 'Show ingest/extract freshness and DB health.',
    category: 'diagnostics',
    risk_level: 'local_safe',
    external_call_risk: 'none',
    supports_dry_run: false,
    supports_print_prompt: false,
    requires_confirmation: false,
    dry_run_required_before_live: false,
    parameters: [],
  },
  {
    command_id: 'brief',
    label: 'Generate Daily Brief',
    description: 'Assemble the markdown daily brief from open signals.',
    category: 'build',
    risk_level: 'local_write',
    external_call_risk: 'none',
    supports_dry_run: true,
    supports_print_prompt: false,
    requires_confirmation: false,
    dry_run_required_before_live: false,
    parameters: [{ name: 'target_date', type: 'str', required: false, default: null, allowed_values: null, help: 'YYYY-MM-DD' }],
  },
  {
    command_id: 'ingest-forecast',
    label: 'Ingest Staffing Forecast',
    description: 'Read the staffing forecast CSV into the local DB.',
    category: 'ingest',
    risk_level: 'local_write',
    external_call_risk: 'none',
    supports_dry_run: true,
    supports_print_prompt: false,
    requires_confirmation: false,
    dry_run_required_before_live: false,
    parameters: [],
  },
  {
    command_id: 'workspace-fetch',
    label: 'Fetch Workspace Activity',
    description: 'Retrieve Gmail/Calendar/Chat activity via Gemini CLI.',
    category: 'workspace',
    risk_level: 'external_bounded',
    external_call_risk: 'possible',
    supports_dry_run: true,
    supports_print_prompt: true,
    requires_confirmation: true,
    dry_run_required_before_live: true,
    parameters: [{ name: 'lookback_days', type: 'int', required: false, default: 7, allowed_values: null, help: 'Days to look back' }],
  },
  {
    command_id: 'project-docs-fetch',
    label: 'Fetch Project Documents',
    description: 'Search Drive for project documents via Gemini CLI.',
    category: 'workspace',
    risk_level: 'external_bounded',
    external_call_risk: 'likely',
    supports_dry_run: true,
    supports_print_prompt: true,
    requires_confirmation: true,
    dry_run_required_before_live: true,
    parameters: [
      { name: 'opportunity_number', type: 'str', required: true, default: null, allowed_values: null, help: 'e.g. OPP-ACME-002' },
    ],
  },
  // --- Guarded live single project-doc fetch (contract: command_id
  // project_docs_fetch_live_single, risk_level=external_bounded,
  // external_call_risk=likely, requires_confirmation=true,
  // dry_run_required_before_live=true) and its supporting dry-run/batch
  // command ids. These are additive alongside the existing hyphenated
  // 'project-docs-fetch' entry above (kept for back-compat with prior tests).
  {
    command_id: 'project_docs_fetch_dry_run',
    label: 'Fetch Project Documents (Dry Run)',
    description: 'Preview a single-project Drive document search without calling Gemini CLI live.',
    category: 'workspace',
    risk_level: 'local_safe',
    external_call_risk: 'none',
    supports_dry_run: true,
    supports_print_prompt: true,
    requires_confirmation: false,
    dry_run_required_before_live: false,
    parameters: [
      { name: 'opportunity_number', type: 'str', required: true, default: null, allowed_values: null, help: 'e.g. OPP-ACME-002' },
      { name: 'limit', type: 'int', required: false, default: 3, allowed_values: null, help: 'Max documents (up to 5)' },
      { name: 'timeout', type: 'int', required: false, default: 60, allowed_values: null, help: 'Timeout seconds (up to 120)' },
    ],
  },
  {
    command_id: 'project_docs_fetch_live_single',
    label: 'Fetch Project Documents (Live, Single)',
    description: 'Live Drive document search for one project via Gemini CLI. Contacts Google Drive.',
    category: 'workspace',
    risk_level: 'external_bounded',
    external_call_risk: 'likely',
    supports_dry_run: true,
    supports_print_prompt: false,
    requires_confirmation: true,
    dry_run_required_before_live: true,
    parameters: [
      { name: 'opportunity_number', type: 'str', required: true, default: null, allowed_values: null, help: 'e.g. OPP-ACME-002' },
      { name: 'limit', type: 'int', required: false, default: 3, allowed_values: null, help: 'Max documents (up to 5)' },
      { name: 'timeout', type: 'int', required: false, default: 60, allowed_values: null, help: 'Timeout seconds (up to 120)' },
    ],
  },
  {
    command_id: 'project_docs_fetch_batch_live_bounded',
    label: 'Fetch Project Documents (Live, Batch)',
    description: 'Live Drive document search across multiple projects via Gemini CLI. Contacts Google Drive.',
    category: 'workspace',
    risk_level: 'external_bounded',
    external_call_risk: 'likely',
    supports_dry_run: true,
    supports_print_prompt: false,
    requires_confirmation: true,
    dry_run_required_before_live: true,
    parameters: [
      { name: 'limit_projects', type: 'int', required: true, default: null, allowed_values: null, help: 'Max projects (bounded, e.g. up to 25)' },
      { name: 'timeout', type: 'int', required: false, default: 60, allowed_values: null, help: 'Timeout seconds' },
    ],
  },
  {
    command_id: 'closeout-weekly',
    label: 'Send Weekly Exec Update',
    description: 'Compose and send the weekly executive update externally.',
    category: 'closeout',
    risk_level: 'external_high_risk',
    external_call_risk: 'high',
    supports_dry_run: false,
    supports_print_prompt: false,
    requires_confirmation: true,
    dry_run_required_before_live: false,
    parameters: [],
  },
  {
    command_id: 'demo-reset',
    label: 'Reset Demo Data',
    description: 'Irreversibly wipe and reseed the local demo database.',
    category: 'admin',
    risk_level: 'blocked',
    external_call_risk: 'none',
    supports_dry_run: true,
    supports_print_prompt: false,
    requires_confirmation: true,
    dry_run_required_before_live: false,
    parameters: [],
  },
]

export const mockRecentRuns: RunRecord[] = [
  {
    run_id: 'run-1001',
    command_id: 'status',
    status: 'success',
    dry_run: false,
    started_at: '2026-06-30T07:58:00Z',
    finished_at: '2026-06-30T07:58:03Z',
    stdout: 'db_path: data/processed/manager_os.duckdb\nall sources fresh',
    stderr: '',
  },
  {
    run_id: 'run-1000',
    command_id: 'brief',
    status: 'success',
    dry_run: false,
    started_at: '2026-06-30T07:50:00Z',
    finished_at: '2026-06-30T07:50:04Z',
    stdout: 'Wrote output/daily_briefs/2026-06-30.md',
    stderr: '',
  },
  {
    run_id: 'run-0999',
    command_id: 'ingest-forecast',
    status: 'success',
    dry_run: true,
    started_at: '2026-06-29T18:12:00Z',
    finished_at: '2026-06-29T18:12:02Z',
    stdout: '[dry-run] would ingest 42 rows',
    stderr: '',
  },
  {
    run_id: 'run-0998',
    command_id: 'workspace-fetch',
    status: 'failed',
    dry_run: false,
    started_at: '2026-06-29T09:03:00Z',
    finished_at: '2026-06-29T09:03:41Z',
    stdout: '',
    stderr: 'Error: Gemini CLI timed out after 180s',
  },
]

function delay<T>(value: T, ms = 100): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

let runCounter = mockRecentRuns.length

const SINGLE_COMMAND_ESTIMATED_TOKENS = 350

/** Fabricates a plausible ValidateResponse for mock/offline mode. Never
 * calls fetch() or executes anything real. */
export function mockValidateCommand(
  commandId: string,
  params: Record<string, unknown>,
): Promise<ValidateResponse> {
  const command = mockCommandRegistry.find((c) => c.command_id === commandId)
  const argv = [commandId, ...Object.entries(params).flatMap(([k, v]) => [`--${k}`, String(v)])]
  return delay(
    {
      ok: true,
      argv_preview: argv,
      risk_level: command?.risk_level ?? 'local_safe',
      external_call_risk: command?.external_call_risk ?? 'none',
      estimated_input_tokens: command?.risk_level === 'local_safe' ? null : SINGLE_COMMAND_ESTIMATED_TOKENS,
      warnings: command?.requires_confirmation
        ? ['This command requires confirmation and is not runnable from this UI yet.']
        : [],
      requires_confirmation: command?.requires_confirmation ?? false,
    },
    150,
  )
}

/** Fabricates a plausible RunResponse for mock/offline mode. Never calls
 * fetch() or executes anything real. */
export function mockRunCommand(
  commandId: string,
  _params: Record<string, unknown>,
  _confirm: boolean,
): Promise<RunResponse> {
  runCounter += 1
  const runId = `run-mock-${runCounter}`
  const stdout = `[mock] "${commandId}" completed (no network call made).`
  mockRecentRuns.unshift({
    run_id: runId,
    command_id: commandId,
    status: 'success',
    dry_run: false,
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    stdout,
    stderr: '',
  })
  return delay(
    {
      ok: true,
      run_id: runId,
      status: 'success',
      command_id: commandId,
      stdout,
      stderr: '',
      error: null,
      estimated_input_tokens: null,
      estimated_output_tokens: null,
    },
    200,
  )
}

/** Fabricates plausible logs for mock/offline mode by looking up the run in
 * `mockRecentRuns`, falling back to a placeholder if not found. */
export function mockRunLogs(runId: string): Promise<RunLogs> {
  const run = mockRecentRuns.find((r) => r.run_id === runId)
  return delay(
    {
      stdout: run?.stdout ?? '[mock] no stdout captured for this run.',
      stderr: run?.stderr ?? '',
      error: null,
    },
    100,
  )
}

