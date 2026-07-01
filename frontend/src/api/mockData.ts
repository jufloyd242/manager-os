import type {
  StatusCardData,
  DailyOperatingLoop,
  CommandDefinition,
  RunRecord,
  TokenBudget,
  ManagerOsApiClient,
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

export const mockCommandRegistry: CommandDefinition[] = [
  {
    command_id: 'status',
    label: 'Status',
    description: 'Show ingest/extract freshness and DB health.',
    risk_level: 'local_safe',
    external_call_risk: 'none',
    supports_dry_run: false,
    requires_confirmation: false,
  },
  {
    command_id: 'brief',
    label: 'Generate Daily Brief',
    description: 'Assemble the markdown daily brief from open signals.',
    risk_level: 'local_write',
    external_call_risk: 'none',
    supports_dry_run: true,
    requires_confirmation: false,
  },
  {
    command_id: 'ingest-forecast',
    label: 'Ingest Staffing Forecast',
    description: 'Read the staffing forecast CSV into the local DB.',
    risk_level: 'local_write',
    external_call_risk: 'none',
    supports_dry_run: true,
    requires_confirmation: false,
  },
  {
    command_id: 'workspace-fetch',
    label: 'Fetch Workspace Activity',
    description: 'Retrieve Gmail/Calendar/Chat activity via Gemini CLI.',
    risk_level: 'external_bounded',
    external_call_risk: 'possible',
    supports_dry_run: true,
    requires_confirmation: false,
  },
  {
    command_id: 'project-docs-fetch',
    label: 'Fetch Project Documents',
    description: 'Search Drive for project documents via Gemini CLI.',
    risk_level: 'external_bounded',
    external_call_risk: 'likely',
    supports_dry_run: true,
    requires_confirmation: true,
  },
  {
    command_id: 'closeout-weekly',
    label: 'Send Weekly Exec Update',
    description: 'Compose and send the weekly executive update externally.',
    risk_level: 'external_high_risk',
    external_call_risk: 'high',
    supports_dry_run: false,
    requires_confirmation: true,
  },
  {
    command_id: 'demo-reset',
    label: 'Reset Demo Data',
    description: 'Irreversibly wipe and reseed the local demo database.',
    risk_level: 'blocked',
    external_call_risk: 'none',
    supports_dry_run: true,
    requires_confirmation: true,
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
  },
  {
    run_id: 'run-1000',
    command_id: 'brief',
    status: 'success',
    dry_run: false,
    started_at: '2026-06-30T07:50:00Z',
    finished_at: '2026-06-30T07:50:04Z',
  },
  {
    run_id: 'run-0999',
    command_id: 'ingest-forecast',
    status: 'success',
    dry_run: true,
    started_at: '2026-06-29T18:12:00Z',
    finished_at: '2026-06-29T18:12:02Z',
  },
  {
    run_id: 'run-0998',
    command_id: 'workspace-fetch',
    status: 'failed',
    dry_run: false,
    started_at: '2026-06-29T09:03:00Z',
    finished_at: '2026-06-29T09:03:41Z',
  },
]

export const mockTokenBudget: TokenBudget = {
  daily_budget_tokens: 200000,
  used_tokens: 68500,
  pending: [
    { command_id: 'project-docs-fetch', label: 'Fetch Project Documents', estimated_input_tokens: 4200 },
    { command_id: 'workspace-fetch', label: 'Fetch Workspace Activity', estimated_input_tokens: 6800 },
  ],
}

function delay<T>(value: T, ms = 100): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

let runCounter = mockRecentRuns.length

/**
 * Mock implementation of ManagerOsApiClient. Never calls fetch() or any
 * network endpoint — all data is fabricated in-memory. `runCommand` only
 * returns a fake RunRecord for the caller to append to local UI state.
 */
export const mockApiClient: ManagerOsApiClient = {
  getSystemStatus: () => delay(mockSystemStatus),
  getDailyOperatingLoop: () => delay(mockDailyOperatingLoop),
  getCommandRegistry: () => delay(mockCommandRegistry),
  getRecentRuns: () => delay(mockRecentRuns),
  getTokenBudget: () => delay(mockTokenBudget),
  runCommand: (commandId, opts) => {
    runCounter += 1
    const startedAt = new Date().toISOString()
    const record: RunRecord = {
      run_id: `run-mock-${runCounter}`,
      command_id: commandId,
      status: 'success',
      dry_run: opts.dryRun,
      started_at: startedAt,
      finished_at: startedAt,
    }
    return delay(record, 200)
  },
}
