import type {
  StatusCardData,
  DailyOperatingLoop,
  CommandSpec,
  RunRecord,
  ValidateResponse,
  RunResponse,
  RunLogs,
  RecommendedAction,
  ActionGroup,
  ActionSummary,
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

// --- Recommended actions (shared between the flat `recommended_actions`
// list and the grouped `action_groups` view below, so titles/ids stay in
// sync rather than drifting between two hand-maintained copies) -----------

const SOW_REVIEW_ACTION: RecommendedAction = {
  title: 'Review Acme Corp SOW before Friday deadline',
  reason: 'SOW deadline in 3 days with no reviewer assigned',
  command: 'manager-os project-docs-fetch --opportunity-number OPP-ACME-002',
  priority: 'high',
  source: 'projects_deals',
}

// Document-gap-sourced action: extends the base RecommendedAction shape
// with the structured command_center fields (contract: id/source/
// entity_type/entity_id/primary_command/secondary_commands). Wired to
// real command_center command ids with prefilled params so
// RecommendedActionCard can render Dry Run Fetch / Print Prompt / Run
// Live Fetch buttons for it.
const INITECH_DOC_GAP_ACTION: RecommendedAction = {
  id: 'doc-gap-initech-discovery-sow',
  title: 'Fetch missing SOW for Initech — Discovery',
  reason: 'Document gap: SOW missing for Initech — Discovery',
  command: 'manager-os project-docs-fetch --opportunity-number OPP-INITECH-004',
  priority: 'medium',
  source: 'document_gaps',
  entity_type: 'project',
  entity_id: 'OPP-INITECH-004',
  primary_command: {
    command_id: 'project_docs_fetch_dry_run',
    params: { opportunity_number: 'OPP-INITECH-004' },
  },
  secondary_commands: [
    {
      label: 'Print Prompt',
      command_id: 'project_docs_fetch_print_prompt',
      params: { opportunity_number: 'OPP-INITECH-004' },
    },
    {
      label: 'Run Live Fetch',
      command_id: 'project_docs_fetch_live_single',
      params: { opportunity_number: 'OPP-INITECH-004' },
      requires_confirmation: true,
      requires_successful_dry_run: true,
    },
  ],
}

const SCHEDULE_1_1_ACTION: RecommendedAction = {
  title: 'Schedule 1:1 with Jordan Lee',
  reason: 'No 1:1 recorded in 18 days',
  command: 'manager-os meeting-prep --meeting jordan-lee-1-1',
  priority: 'medium',
  source: 'people_staffing',
}

const OVERALLOCATION_ACTION: RecommendedAction = {
  title: 'Investigate Priya Nair overallocation',
  reason: '128% allocation over next 2 weeks',
  command: 'manager-os extract --entity person',
  priority: 'high',
  source: 'people_staffing',
}

const GLOBEX_FOLLOWUP_ACTION: RecommendedAction = {
  title: 'Follow up on Globex renewal',
  reason: 'Deal stalled with no activity in 21 days',
  command: 'manager-os brief --date 2026-06-30',
  priority: 'low',
  source: 'projects_deals',
}

// Additional generated document-gap actions purely to give the Action Inbox
// a realistic "overflow" group (45 total) to exercise the "show top 5,
// expand" behavior. Deterministic and side-effect free.
const EXTRA_DOC_GAP_CLIENTS = [
  'Acme Corp', 'Initech', 'Globex', 'Contoso', 'Umbrella Inc',
  'Stark Industries', 'Wayne Enterprises', 'Wonka Industries', 'Hooli', 'Soylent Corp',
]
const EXTRA_DOC_GAP_MISSING = ['Closure deck', 'SOW', 'Discovery notes', 'Architecture doc', 'Runbook']

function buildGeneratedDocGapAction(n: number): RecommendedAction {
  const client = EXTRA_DOC_GAP_CLIENTS[n % EXTRA_DOC_GAP_CLIENTS.length]
  const missing = EXTRA_DOC_GAP_MISSING[n % EXTRA_DOC_GAP_MISSING.length]
  const opp = `OPP-DOCGAP-${String(n + 1).padStart(3, '0')}`
  const project = `${client} — Project ${n + 1}`
  return {
    id: `document_gap:${opp}`,
    title: `Fetch missing ${missing} for ${project}`,
    reason: `Document gap: ${missing} missing for ${project}`,
    command: `manager-os project-docs-fetch --opportunity-number ${opp}`,
    priority: n % 5 === 0 ? 'high' : 'medium',
    source: 'document_gaps',
    entity_type: 'project',
    entity_id: opp,
    primary_command: {
      command_id: 'project_docs_fetch_dry_run',
      params: { opportunity_number: opp },
    },
    secondary_commands: [
      {
        label: 'Print Prompt',
        command_id: 'project_docs_fetch_print_prompt',
        params: { opportunity_number: opp },
      },
      {
        label: 'Run Live Fetch',
        command_id: 'project_docs_fetch_live_single',
        params: { opportunity_number: opp },
        requires_confirmation: true,
        requires_successful_dry_run: true,
      },
    ],
  }
}

const DOCUMENT_GAP_GROUP_ACTIONS: RecommendedAction[] = [
  INITECH_DOC_GAP_ACTION,
  ...Array.from({ length: 44 }, (_, i) => buildGeneratedDocGapAction(i + 1)),
]
const PEOPLE_STAFFING_GROUP_ACTIONS: RecommendedAction[] = [SCHEDULE_1_1_ACTION, OVERALLOCATION_ACTION]
const PROJECTS_DEALS_GROUP_ACTIONS: RecommendedAction[] = [SOW_REVIEW_ACTION, GLOBEX_FOLLOWUP_ACTION]

// Mirrors `_is_executable` in `manager_os.build.daily_action_groups`: an
// action counts as executable if it has a primary_command OR any
// secondary_commands, not just primary_command alone.
function isExecutable(action: RecommendedAction): boolean {
  return Boolean(action.primary_command) || Boolean(action.secondary_commands && action.secondary_commands.length > 0)
}

function computeActionSummary(groups: ActionGroup[]): ActionSummary {
  const allActions = groups.flatMap((g) => g.actions)
  const by_source: Record<string, number> = {}
  const by_priority = { high: 0, medium: 0, low: 0 }
  let executable = 0
  let informational = 0
  for (const action of allActions) {
    const source = action.source ?? 'other'
    by_source[source] = (by_source[source] ?? 0) + 1
    by_priority[action.priority] += 1
    if (isExecutable(action)) executable += 1
    else informational += 1
  }
  return { total: allActions.length, by_source, by_priority, executable, informational }
}

export const mockActionGroups: ActionGroup[] = [
  {
    id: 'document_gaps',
    title: 'Document Gaps',
    source: 'document_gaps',
    count: DOCUMENT_GAP_GROUP_ACTIONS.length,
    priority: 'high',
    summary: `${DOCUMENT_GAP_GROUP_ACTIONS.length} projects missing required documents — review and fetch via Drive search`,
    default_visible_count: 5,
    actions: DOCUMENT_GAP_GROUP_ACTIONS,
  },
  {
    id: 'people_staffing',
    title: 'People / Staffing',
    source: 'people_staffing',
    count: PEOPLE_STAFFING_GROUP_ACTIONS.length,
    priority: 'medium',
    summary: `${PEOPLE_STAFFING_GROUP_ACTIONS.length} staffing signals need attention`,
    default_visible_count: 5,
    actions: PEOPLE_STAFFING_GROUP_ACTIONS,
  },
  {
    id: 'projects_deals',
    title: 'Projects / Deals',
    source: 'projects_deals',
    count: PROJECTS_DEALS_GROUP_ACTIONS.length,
    priority: 'high',
    summary: `${PROJECTS_DEALS_GROUP_ACTIONS.length} project/deal signals need follow-up`,
    default_visible_count: 5,
    actions: PROJECTS_DEALS_GROUP_ACTIONS,
  },
]

export const mockActionSummary: ActionSummary = computeActionSummary(mockActionGroups)

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
    SOW_REVIEW_ACTION,
    INITECH_DOC_GAP_ACTION,
    SCHEDULE_1_1_ACTION,
    OVERALLOCATION_ACTION,
    GLOBEX_FOLLOWUP_ACTION,
  ],
  unfiltered_recommended_actions: [
    SOW_REVIEW_ACTION,
    INITECH_DOC_GAP_ACTION,
    SCHEDULE_1_1_ACTION,
    OVERALLOCATION_ACTION,
    GLOBEX_FOLLOWUP_ACTION,
  ],
  action_summary: mockActionSummary,
  action_groups: mockActionGroups,
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
      { name: 'limit', type: 'int', required: false, default: 3, maximum: 5, allowed_values: null, help: 'Max documents (up to 5)' },
      { name: 'timeout', type: 'int', required: false, default: 60, maximum: 120, allowed_values: null, help: 'Timeout seconds (up to 120)' },
    ],
  },
  {
    command_id: 'project_docs_fetch_print_prompt',
    label: 'Fetch Project Documents (Print Prompt)',
    description: 'Print the Drive search prompt for a single project without calling Gemini CLI.',
    category: 'workspace',
    risk_level: 'local_safe',
    external_call_risk: 'none',
    supports_dry_run: false,
    supports_print_prompt: true,
    requires_confirmation: false,
    dry_run_required_before_live: false,
    parameters: [
      { name: 'opportunity_number', type: 'str', required: true, default: null, allowed_values: null, help: 'e.g. OPP-ACME-002' },
      { name: 'limit', type: 'int', required: false, default: 3, maximum: 5, allowed_values: null, help: 'Max documents (up to 5)' },
      { name: 'timeout', type: 'int', required: false, default: 60, maximum: 120, allowed_values: null, help: 'Timeout seconds (up to 120)' },
    ],
  },
  {
    command_id: 'project_docs_fetch_live_single',
    label: 'Fetch Project Documents (Live, Single)',
    description: 'Live Drive document search for one project via Gemini CLI. Contacts Google Drive.',
    category: 'workspace',
    risk_level: 'external_bounded',
    external_call_risk: 'likely',
    supports_dry_run: false,
    supports_print_prompt: false,
    requires_confirmation: true,
    dry_run_required_before_live: true,
    related_dry_run_command: 'project_docs_fetch_dry_run',
    related_print_prompt_command: 'project_docs_fetch_print_prompt',
    parameters: [
      { name: 'opportunity_number', type: 'str', required: true, default: null, allowed_values: null, help: 'e.g. OPP-ACME-002' },
      { name: 'limit', type: 'int', required: false, default: 3, maximum: 5, allowed_values: null, help: 'Max documents (up to 5)' },
      { name: 'timeout', type: 'int', required: false, default: 60, maximum: 120, allowed_values: null, help: 'Timeout seconds (up to 120)' },
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

