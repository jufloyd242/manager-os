// Typed API client for Manager OS's Command Tower frontend.
//
// Every exported "load"/"action" function here tries a real fetch against
// the Manager OS FastAPI backend first. If the network call fails (network
// error, connection refused, or a non-2xx response) it transparently falls
// back to the static mock data in `mockData.ts` and reports that fact via
// the `isMock` flag on the returned `ApiResult`. Callers (components) are
// expected to surface `isMock` as a visible "offline/mock data" indicator —
// never fail silently and never pretend mock data is live data.
//
// Base URL comes from the Vite env var VITE_MANAGER_OS_API_BASE_URL, falling
// back to http://127.0.0.1:8000 for local dev against a real backend.

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

export interface StatusCardData {
  id: string
  label: string
  detail: string
  freshness: Freshness
  count?: number
  last_source_date?: string | null
  last_successful_fetch?: string | null
  last_successful_ingest?: string | null
  calculated_age?: string | null
  explanation?: string | null
}

/** A single command reference used inside a `RecommendedAction`'s
 * `primary_command` field: the command to run and its prefilled params. */
export interface RecommendedActionCommand {
  command_id: string
  params: Record<string, unknown>
}

/** One of the follow-up commands offered alongside a recommended action's
 * primary command (e.g. "Print Prompt", "Run Live Fetch"). */
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
  /** Existing human-readable command string, kept for back-compat. */
  command: string
  priority: Priority
  /** Extended structured-action fields (optional — informational-only
   * actions, e.g. people_staffing/meeting-prep signals, omit these and
   * continue to render as plain text with no buttons). */
  id?: string
  source?: string
  entity_type?: string
  entity_id?: string
  primary_command?: RecommendedActionCommand
  secondary_commands?: RecommendedActionSecondaryCommand[]
  why_it_matters?: string
  recommended_next_action?: string
  entity?: string
  source_date?: string
  last_refreshed?: string
  confidence?: number
  explanation?: string
}

/** Aggregate counts over the full set of recommended actions, used to render
 * the Action Inbox header. Optional on `DailyOperatingLoop` — an older
 * backend that doesn't compute this yet simply omits the field. */
export interface ActionSummary {
  total: number
  by_source: Record<string, number>
  by_priority: { high: number; medium: number; low: number }
  executable: number
  informational: number
}

/** A named, pre-grouped bucket of recommended actions (e.g. all document-gap
 * actions), used to render a collapsible card in the Action Inbox instead of
 * one long flat list. Optional on `DailyOperatingLoop` — when absent, the UI
 * falls back to grouping `recommended_actions` client-side by `source`. */
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

/**
 * Mirrors the exact shape of Python's `build_daily_operating_loop()` dict
 * (see `src/manager_os/build/daily_operating_loop.py`) / the `/api/daily`
 * response.
 */
export interface DailyOperatingLoop {
  date: string
  people_staffing: unknown[]
  meetings: unknown[]
  projects_deals: unknown[]
  document_gaps: unknown[]
  feedback_learning: unknown[]
  recommended_actions: RecommendedAction[]
  warnings: string[]
  /** Optional — grouped/summarized view of `recommended_actions` for the
   * Action Inbox. Absent on older backends; the UI degrades gracefully. */
  action_summary?: ActionSummary
  action_groups?: ActionGroup[]
  /** Full pre-cap list of recommended actions before filtering/capping.
   * Not rendered in the default Action Inbox — available for advanced or
   * secondary views. */
  unfiltered_recommended_actions?: RecommendedAction[]
}

/** A single typed parameter declared on a command (from the command registry). */
export interface ParameterSpec {
  name: string
  type: ParameterType
  required: boolean
  default: unknown
  allowed_values: unknown[] | null
  help: string
  /** Optional upper bound for numeric params (e.g. `limit` max=5, `timeout` max=120).
   * Optional so existing entries that don't declare a bound continue to type-check. */
  maximum?: number | null
}

/**
 * Command registry entry — mirrors `manager_os.command_center.models.CommandSpec`
 * as exposed by (the agreed contract for) `GET /api/commands` / `GET /api/commands/{id}`.
 */
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
  /** Companion command ids surfaced by the registry for guarded flows (e.g.
   * `project_docs_fetch_live_single` points at its dry-run/print-prompt
   * companions). Optional so existing entries without these continue to
   * type-check. */
  related_dry_run_command?: string | null
  related_print_prompt_command?: string | null
}

/** Response shape for `POST /api/commands/{id}/validate`.
 *
 * `command_id`, `dry_run_required_before_live`, and `estimated_output_tokens`
 * are optional extensions to support guarded external-call commands (e.g.
 * `project_docs_fetch_live_single`) — kept optional so existing call sites
 * that only supply the original fields continue to type-check. */
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

/** Response shape for `POST /api/commands/{id}/run`. */
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

/** One row of run history, as returned by `GET /api/runs`. */
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

/** Response shape for `GET /api/runs/{run_id}/logs`. */
export interface RunLogs {
  stdout: string | null
  stderr: string | null
  error: string | null
}

/** The most recent token-cost estimate shown by `TokenBudgetPanel`. */
export interface TokenEstimate {
  command_id: string
  label: string
  risk_level: RiskLevel
  estimated_input_tokens: number | null
}

/** Wraps every client call with a flag saying whether the data came from
 * the real API (`isMock: false`) or the mock fallback (`isMock: true`). */
export interface ApiResult<T> {
  data: T
  isMock: boolean
}

const API_BASE_URL: string =
  (import.meta.env.VITE_MANAGER_OS_API_BASE_URL as string | undefined) || 'http://127.0.0.1:8000'

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init)
  if (!res.ok) {
    throw new Error(`Manager OS API request to ${path} failed with status ${res.status}`)
  }
  return (await res.json()) as T
}

export class ApiUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ApiUnavailableError'
  }
}

export function isDemoModeOrTest(): boolean {
  const isTest = typeof window !== 'undefined' && (
    (window as any).isTestEnvironment ||
    (window as any).vitest ||
    (window as any).__vite_plugin_react_preamble_installed__ === undefined
  );
  const isDemo = typeof localStorage !== 'undefined' && localStorage.getItem('manager_os_demo_mode') === 'true';
  return !!(isTest || isDemo);
}

async function withMockFallback<T>(
  live: () => Promise<T>,
  fallback: () => T | Promise<T>,
): Promise<ApiResult<T>> {
  try {
    const data = await live()
    return { data, isMock: false }
  } catch (error) {
    if (isDemoModeOrTest()) {
      const data = await fallback()
      return { data, isMock: true }
    }
    throw new ApiUnavailableError('Manager OS API is unavailable')
  }
}

// --- Status ---------------------------------------------------------------

interface RawSourceHealth {
  name: string
  status: string
  count: number
  last_updated: string | null
  warnings: string[]
  last_source_date?: string | null
  last_successful_fetch?: string | null
  last_successful_ingest?: string | null
  calculated_age?: string | null
  freshness: string
  explanation?: string | null
}

interface RawStatusResponse {
  ok: boolean
  db_path: string
  workspace_enabled: boolean
  sources: RawSourceHealth[]
  warnings: string[]
}

function mapStatusResponse(raw: RawStatusResponse): StatusCardData[] {
  return raw.sources.map((s) => ({
    id: s.name,
    label: s.name,
    detail: s.explanation ?? s.warnings[0] ?? (s.last_updated ? `Last updated ${s.last_updated}` : `${s.count} rows`),
    freshness: (s.freshness as Freshness) || 'unknown',
    count: s.count,
    last_source_date: s.last_source_date,
    last_successful_fetch: s.last_successful_fetch,
    last_successful_ingest: s.last_successful_ingest,
    calculated_age: s.calculated_age,
    explanation: s.explanation,
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

// --- Staffing Balance --------------------------------------------------------

export interface AllocationComparison {
  person: string
  original_allocation: number
  balanced_allocation: number
}

export interface Redistribution {
  from_person: string
  to_person: string
  amount: number
  project?: string
}

export interface StaffingBalanceResponse {
  comparison: AllocationComparison[]
  redistributions: Redistribution[]
}

export function getStaffingBalance(): Promise<ApiResult<StaffingBalanceResponse>> {
  return withMockFallback(
    () => requestJson<StaffingBalanceResponse>('/api/analytics/staffing-balance'),
    () => ({
      comparison: [
        { person: 'Priya Nair', original_allocation: 1.28, balanced_allocation: 1.0 },
        { person: 'Jordan Lee', original_allocation: 0.8, balanced_allocation: 1.08 },
      ],
      redistributions: [
        { from_person: 'Priya Nair', to_person: 'Jordan Lee', amount: 0.28, project: 'Acme Corp — Phase 2' },
      ],
    }),
  )
}

export function postFeedback(itemId: string, rating: string, reason?: string): Promise<ApiResult<{ ok: boolean; event_id: string }>> {
  return withMockFallback(
    () =>
      requestJson<{ ok: boolean; event_id: string }>('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_id: itemId, rating, reason }),
      }),
    () => ({ ok: true, event_id: 'mock-event' }),
  )
}

export function runSafeRefresh(): Promise<ApiResult<{ ok: boolean; message: string }>> {
  return withMockFallback(
    () =>
      requestJson<{ ok: boolean; message: string }>('/api/refresh', {
        method: 'POST',
      }),
    () => ({ ok: true, message: 'Local refresh completed successfully.' }),
  )
}
