// Typed API client for Manager OS's Command Tower frontend.
//
// Normal runtime: API success → return data. Network failure or non-2xx →
// throw ManagerOsApiError. No implicit mock fallback.
//
// Explicit demo mode: set VITE_MANAGER_OS_DEMO_MODE=true to enable mock
// fallback when the backend is unavailable.

import {
  mockSystemStatus,
  mockDailyOperatingLoop,
  mockCommandRegistry,
  mockRecentRuns,
  mockValidateCommand,
  mockRunCommand,
  mockRunLogs,
} from './mockData'

export type RiskLevel =
  | 'local_safe'
  | 'local_write'
  | 'external_bounded'
  | 'external_high_risk'
  | 'blocked'

export type ExternalCallRisk = 'none' | 'possible' | 'likely' | 'high'

export type Priority = 'high' | 'medium' | 'low'

export type RunStatus = 'success' | 'failed' | 'running' | 'skipped' | 'blocked' | 'error' | 'ok'

export type Freshness = 'fresh' | 'stale' | 'missing' | 'unknown'

export type ParameterType = 'str' | 'int' | 'float' | 'bool' | 'list'

export class ManagerOsApiError extends Error {
  endpoint: string
  status?: number
  kind: 'network' | 'http' | 'parse' | 'unavailable'

  constructor(
    message: string,
    endpoint: string,
    kind: 'network' | 'http' | 'parse' | 'unavailable' = 'unavailable',
    status?: number,
  ) {
    super(message)
    this.name = 'ManagerOsApiError'
    this.endpoint = endpoint
    this.kind = kind
    this.status = status
  }
}

export interface StatusCardData {
  id: string
  label: string
  detail: string
  freshness: Freshness
  count?: number
}

export interface RecommendedActionCommand {
  command_id: string
  params: Record<string, unknown>
}

export interface RecommendedActionSecondaryCommand {
  label: string
  command_id: string
  params: Record<string, unknown>
  requires_confirmation?: boolean
  requires_successful_dry_run?: boolean
}

export interface RecommendedAction {
  title: string
  reason: string
  command: string
  priority: Priority
  id?: string
  source?: string
  entity_type?: string
  entity_id?: string
  primary_command?: RecommendedActionCommand
  secondary_commands?: RecommendedActionSecondaryCommand[]
}

export interface ActionSummary {
  total: number
  by_source: Record<string, number>
  by_priority: { high: number; medium: number; low: number }
  executable: number
  informational: number
}

export interface ActionGroup {
  id: string
  title: string
  source: string
  count: number
  priority: string
  summary: string
  default_visible_count: number
  actions: RecommendedAction[]
}

export interface DailyOperatingLoop {
  date: string
  people_staffing: unknown[]
  meetings: unknown[]
  projects_deals: unknown[]
  document_gaps: unknown[]
  feedback_learning: unknown[]
  recommended_actions: RecommendedAction[]
  warnings: string[]
  action_summary?: ActionSummary
  action_groups?: ActionGroup[]
}

export interface ParameterSpec {
  name: string
  type: ParameterType
  required: boolean
  default: unknown
  allowed_values: unknown[] | null
  help: string
  maximum?: number | null
}

export interface CommandSpec {
  command_id: string
  label: string
  description: string
  category: string
  risk_level: RiskLevel
  external_call_risk: ExternalCallRisk
  supports_dry_run: boolean
  supports_print_prompt: boolean
  requires_confirmation: boolean
  dry_run_required_before_live: boolean
  parameters: ParameterSpec[]
  related_dry_run_command?: string | null
  related_print_prompt_command?: string | null
}

export interface ValidateResponse {
  ok: boolean
  command_id?: string
  argv_preview: string[] | null
  risk_level: RiskLevel
  external_call_risk: ExternalCallRisk
  requires_confirmation: boolean
  dry_run_required_before_live?: boolean
  estimated_input_tokens: number | null
  estimated_output_tokens?: number | null
  warnings: string[]
}

export interface RunResponse {
  ok: boolean
  run_id: string
  status: string
  command_id: string
  stdout: string | null
  stderr: string | null
  error: string | null
  estimated_input_tokens: number | null
  estimated_output_tokens: number | null
}

export interface RunRecord {
  run_id: string
  command_id: string
  status: RunStatus
  dry_run: boolean
  started_at: string
  finished_at: string | null
  stdout?: string | null
  stderr?: string | null
}

export interface RunLogs {
  stdout: string | null
  stderr: string | null
  error: string | null
}

export interface TokenEstimate {
  command_id: string
  label: string
  risk_level: RiskLevel
  estimated_input_tokens: number | null
}

export interface ApiResult<T> {
  data: T
  isMock: boolean
}

const API_BASE_URL: string =
  (import.meta.env.VITE_MANAGER_OS_API_BASE_URL as string | undefined) || 'http://localhost:8000'

const DEMO_MODE = import.meta.env.VITE_MANAGER_OS_DEMO_MODE === 'true'

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, init)
  } catch {
    throw new ManagerOsApiError(
      `Manager OS API is unreachable at ${API_BASE_URL}${path}`,
      path,
      'network',
    )
  }
  if (!res.ok) {
    throw new ManagerOsApiError(
      `Manager OS API request to ${path} failed with status ${res.status}`,
      path,
      'http',
      res.status,
    )
  }
  try {
    return (await res.json()) as T
  } catch {
    throw new ManagerOsApiError(
      `Manager OS API returned invalid JSON from ${path}`,
      path,
      'parse',
    )
  }
}

async function withMockFallback<T>(
  live: () => Promise<T>,
  fallback: () => T | Promise<T>,
): Promise<ApiResult<T>> {
  if (!DEMO_MODE) {
    const data = await live()
    return { data, isMock: false }
  }
  try {
    const data = await live()
    return { data, isMock: false }
  } catch {
    const data = await fallback()
    return { data, isMock: true }
  }
}

// --- Status ---------------------------------------------------------------

interface RawSourceHealth {
  name: string
  status: string
  count: number
  last_updated: string | null
  warnings: string[]
}

interface RawStatusResponse {
  ok: boolean
  db_path: string
  workspace_enabled: boolean
  sources: RawSourceHealth[]
  warnings: string[]
}

function mapSourceStatusToFreshness(status: string): Freshness {
  if (status === 'available') return 'fresh'
  if (status === 'empty') return 'stale'
  return 'missing'
}

function mapStatusResponse(raw: RawStatusResponse): StatusCardData[] {
  return raw.sources.map((s) => ({
    id: s.name,
    label: s.name,
    detail: s.warnings[0] ?? (s.last_updated ? `Last updated ${s.last_updated}` : `${s.count} rows`),
    freshness: mapSourceStatusToFreshness(s.status),
    count: s.count,
  }))
}

export function getStatus(): Promise<ApiResult<StatusCardData[]>> {
  return withMockFallback(
    async () => mapStatusResponse(await requestJson<RawStatusResponse>('/api/status')),
    () => mockSystemStatus,
  )
}

// --- Daily operating loop ---------------------------------------------------

export function getDaily(date?: string): Promise<ApiResult<DailyOperatingLoop>> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ''
  return withMockFallback(
    () => requestJson<DailyOperatingLoop>(`/api/daily${query}`),
    () => mockDailyOperatingLoop,
  )
}

// --- Command registry -------------------------------------------------------

export function getCommands(): Promise<ApiResult<CommandSpec[]>> {
  return withMockFallback(
    () => requestJson<CommandSpec[]>('/api/commands'),
    () => mockCommandRegistry,
  )
}

export function getCommand(commandId: string): Promise<ApiResult<CommandSpec | null>> {
  return withMockFallback(
    () => requestJson<CommandSpec>(`/api/commands/${encodeURIComponent(commandId)}`),
    () => mockCommandRegistry.find((c) => c.command_id === commandId) ?? null,
  )
}

export function validateCommand(
  commandId: string,
  params: Record<string, unknown>,
): Promise<ApiResult<ValidateResponse>> {
  return withMockFallback(
    () =>
      requestJson<ValidateResponse>(`/api/commands/${encodeURIComponent(commandId)}/validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params }),
      }),
    () => mockValidateCommand(commandId, params),
  )
}

export function runCommand(
  commandId: string,
  params: Record<string, unknown>,
  confirm = false,
): Promise<ApiResult<RunResponse>> {
  return withMockFallback(
    () =>
      requestJson<RunResponse>(`/api/commands/${encodeURIComponent(commandId)}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params, confirm }),
      }),
    () => mockRunCommand(commandId, params, confirm),
  )
}

// --- Run history -------------------------------------------------------------

interface RawRunListResponse {
  runs: RunRecord[]
}

export function getRuns(limit?: number): Promise<ApiResult<RunRecord[]>> {
  const query = typeof limit === 'number' ? `?limit=${limit}` : ''
  return withMockFallback(
    async () => (await requestJson<RawRunListResponse>(`/api/runs${query}`)).runs,
    () => mockRecentRuns,
  )
}

export function getRunLogs(runId: string): Promise<ApiResult<RunLogs>> {
  return withMockFallback(
    () => requestJson<RunLogs>(`/api/runs/${encodeURIComponent(runId)}/logs`),
    () => mockRunLogs(runId),
  )
}

// --- Meetings ----------------------------------------------------------------

export interface MeetingEvent {
  id: string
  meeting_date: string
  start_time: string
  end_time?: string
  location?: string
  description_summary?: string
  title: string
  attendees: string[]
  linked_entities?: unknown[]
  source: string
  external_id: string
}

export interface MeetingsResponse {
  date: string
  meetings: MeetingEvent[]
  sync_info?: { last_synced: string | null; source: string; stale: boolean }
  warnings: string[]
}

export interface CalendarSyncResponse {
  ok: boolean
  date: string
  meetings: MeetingEvent[]
  retrieved_at: string
  source: string
  warnings: string[]
  errors: string[]
}

export interface MeetingPrepResponse {
  meeting_id: string
  meeting_title: string
  meeting_date: string | null
  meeting_time: string
  attendees: string[]
  resolved_attendees: {
    person_name: string
    relationship: string | null
    evidence_source: string
    evidence_path: string
    warnings: string[]
  }[]
  matched_rule_id: string
  matched_rule_name: string
  meeting_type: string
  prep_required: boolean
  why_this_rule_matched: string
  sections: Record<string, unknown[]>
  sources_consulted: string[]
  sources_selected: string[]
  sources_excluded: string[]
  missing_context_warnings: string[]
  llm_enriched: boolean
  generated_at: string
}

export function getMeetings(date: string): Promise<ApiResult<MeetingsResponse>> {
  return withMockFallback(
    () => requestJson<MeetingsResponse>(`/api/meetings?date=${encodeURIComponent(date)}`),
    () => ({ date, meetings: [], warnings: [] }),
  )
}

export function syncCalendar(date: string): Promise<ApiResult<CalendarSyncResponse>> {
  return withMockFallback(
    () =>
      requestJson<CalendarSyncResponse>('/api/meetings/sync-calendar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date }),
      }),
    () => ({ ok: true, date, meetings: [], retrieved_at: '', source: '', warnings: [], errors: [] }),
  )
}

export function getMeetingPrep(meetingId: string): Promise<ApiResult<MeetingPrepResponse | null>> {
  return withMockFallback(
    () => requestJson<MeetingPrepResponse>(`/api/meetings/${encodeURIComponent(meetingId)}/prep`),
    () => null,
  )
}

export function regeneratePrep(meetingId: string): Promise<ApiResult<MeetingPrepResponse | null>> {
  return withMockFallback(
    () =>
      requestJson<MeetingPrepResponse>(`/api/meetings/${encodeURIComponent(meetingId)}/prep`, {
        method: 'POST',
      }),
    () => null,
  )
}

// --- Deals -------------------------------------------------------------------

export interface DealEntry {
  account: string
  deal_name: string
  deal_id: string
  stage: string
  close_date: string | null
  days_until_close: number | null
  technical_owner: string
  ae_name: string
  loe_status: string
  sow_status: string
  staffing_feasibility: string
  blockers: string
  next_action: string
  open_signal_count: number
  highest_severity: string | null
  attention_level: string
  attention_reasons: string[]
  freshness: string
  freshness_explanation: string
  forecast_category?: string
  probability?: number | null
  services_amount?: number | null
  sow_title?: string
  sow_url?: string
}

export interface DealsResponse {
  deals: DealEntry[]
  total: number
  attention_count: number
  counts_by_severity: Record<string, number>
  freshness: string
  last_updated: string | null
  warnings: string[]
}

export function getDeals(params?: {
  search?: string
  attention_only?: boolean
  stage?: string
  owner?: string
  limit?: number
}): Promise<ApiResult<DealsResponse>> {
  const query = new URLSearchParams()
  if (params?.search) query.set('search', params.search)
  if (params?.attention_only) query.set('attention_only', 'true')
  if (params?.stage) query.set('stage', params.stage)
  if (params?.owner) query.set('owner', params.owner)
  if (params?.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return withMockFallback(
    () => requestJson<DealsResponse>(`/api/deals${qs ? '?' + qs : ''}`),
    () => ({ deals: [], total: 0, attention_count: 0, counts_by_severity: {}, freshness: 'missing', last_updated: null, warnings: ['Backend unavailable'] }),
  )
}

// --- Forecast ----------------------------------------------------------------

export interface ForecastPersonEntry {
  person_name: string
  planned_hours: number
  target_hours: number | null
  allocation_pct: number
  projects: string[]
  warning: string | null
  classification: string
  roll_off: { week: string; reason: string } | null
}

export interface ForecastResponse {
  selected_week: string | null
  selection_explanation: string
  available_weeks: string[]
  people: ForecastPersonEntry[]
  detail_rows: unknown[]
  row_count: number
  person_count: number
  exception_count: number
  status_counts: Record<string, number>
  freshness: string
  last_ingestion: string | null
  warnings: string[]
}

export function getForecast(params?: {
  week_start?: string
  person?: string
  client?: string
  exceptions_only?: boolean
  limit?: number
}): Promise<ApiResult<ForecastResponse>> {
  const query = new URLSearchParams()
  if (params?.week_start) query.set('week_start', params.week_start)
  if (params?.person) query.set('person', params.person)
  if (params?.client) query.set('client', params.client)
  if (params?.exceptions_only) query.set('exceptions_only', 'true')
  if (params?.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return withMockFallback(
    () => requestJson<ForecastResponse>(`/api/forecast${qs ? '?' + qs : ''}`),
    () => ({ selected_week: null, selection_explanation: 'Unavailable', available_weeks: [], people: [], detail_rows: [], row_count: 0, person_count: 0, exception_count: 0, status_counts: {}, freshness: 'missing', last_ingestion: null, warnings: ['Backend unavailable'] }),
  )
}

// --- People ------------------------------------------------------------------

export interface PeopleResponse {
  people: Array<{
    id: string
    name: string
    role: string
    current_client: string | null
    allocation_pct: number | null
    next_availability_date: string | null
    last_1on1_date: string | null
    morale_signal: string | null
    growth_topic: string | null
    blockers: string | null
  }>
  warnings: string[]
}

export function getPeople(): Promise<ApiResult<PeopleResponse>> {
  return withMockFallback(
    () => requestJson<PeopleResponse>('/api/people'),
    () => ({ people: [], warnings: ['Backend unavailable'] }),
  )
}

// --- Projects ----------------------------------------------------------------

export interface ProjectsResponse {
  projects: Array<Record<string, unknown>>
  warnings: string[]
}

export function getProjects(): Promise<ApiResult<ProjectsResponse>> {
  return withMockFallback(
    () => requestJson<ProjectsResponse>('/api/projects'),
    () => ({ projects: [], warnings: ['Backend unavailable'] }),
  )
}

// --- Workspace Context -------------------------------------------------------

export interface WorkspaceContextItem {
  source_type: string
  source_path: string
  source_date: string | null
  entity_type: string
  entity_name: string
  link_method: string
  link_evidence: string
  confidence: string
  title: string
  excerpt: string
  is_attention: boolean
  is_action: boolean
  why_this_context: string
}

export interface WorkspaceContextResponse {
  selected_date: string
  lookback_start: string
  latest_actual_source_date: string | null
  context_items: WorkspaceContextItem[]
  linked_count: number
  unlinked_count: number
  attention_count: number
  freshness: string
  warnings: string[]
}

export function getWorkspaceContext(params?: {
  date?: string
  lookback_days?: number
  entity_type?: string
  entity?: string
  attention_only?: boolean
  limit?: number
}): Promise<ApiResult<WorkspaceContextResponse>> {
  const query = new URLSearchParams()
  if (params?.date) query.set('date', params.date)
  if (params?.lookback_days !== undefined) query.set('lookback_days', String(params.lookback_days))
  if (params?.entity_type) query.set('entity_type', params.entity_type)
  if (params?.entity) query.set('entity', params.entity)
  if (params?.attention_only) query.set('attention_only', 'true')
  if (params?.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return withMockFallback(
    () => requestJson<WorkspaceContextResponse>(`/api/workspace-context${qs ? '?' + qs : ''}`),
    () => ({ selected_date: '', lookback_start: '', latest_actual_source_date: null, context_items: [], linked_count: 0, unlinked_count: 0, attention_count: 0, freshness: 'missing', warnings: ['Backend unavailable'] }),
  )
}

// --- Refresh -----------------------------------------------------------------

export interface RefreshResult {
  ok: boolean
  date: string
  sources: Record<string, { status: string; source: string; ingested?: number; skipped?: number; failed?: number; warnings?: string[]; error?: string; reason?: string }>
  extraction: { ok: boolean; results: Record<string, unknown> } | null
}

export function postRefresh(body?: {
  date?: string
  sources?: string[]
  run_extraction?: boolean
}): Promise<ApiResult<RefreshResult>> {
  return withMockFallback(
    () =>
      requestJson<RefreshResult>('/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }),
    () => ({ ok: false, date: '', sources: {}, extraction: null }),
  )
}