// Typed API contract for Manager OS's Command Tower frontend.
//
// This module defines the shape of everything the dashboard needs. Today it is
// backed only by `mockApiClient` in `mockData.ts` — no network calls are made.
// To wire up the real backend later, implement `ManagerOsApiClient` with
// `fetch()` calls against the real API and swap the import in `App.tsx`.

export type RiskLevel =
  | 'local_safe'
  | 'local_write'
  | 'external_bounded'
  | 'external_high_risk'
  | 'blocked'

export type ExternalCallRisk = 'none' | 'possible' | 'likely' | 'high'

export type Priority = 'high' | 'medium' | 'low'

export type RunStatus = 'success' | 'failed' | 'running' | 'skipped'

export type Freshness = 'fresh' | 'stale' | 'missing'

export interface StatusCardData {
  id: string
  label: string
  detail: string
  freshness: Freshness
  count?: number
}

export interface RecommendedAction {
  title: string
  reason: string
  command: string
  priority: Priority
}

/**
 * Mirrors the exact shape of Python's `build_daily_operating_loop()` dict
 * (see `src/manager_os/build/daily_operating_loop.py`) so this can be wired
 * to the real API with minimal translation:
 * {date, people_staffing, meetings, projects_deals, document_gaps,
 *  feedback_learning, recommended_actions, warnings}
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
}

/** Mirrors the concepts of a (planned) backend command registry entry. */
export interface CommandDefinition {
  command_id: string
  label: string
  description: string
  risk_level: RiskLevel
  external_call_risk: ExternalCallRisk
  supports_dry_run: boolean
  requires_confirmation: boolean
}

export interface RunRecord {
  run_id: string
  command_id: string
  status: RunStatus
  dry_run: boolean
  started_at: string
  finished_at: string | null
}

export interface TokenBudgetEntry {
  command_id: string
  label: string
  estimated_input_tokens: number
}

export interface TokenBudget {
  daily_budget_tokens: number
  used_tokens: number
  pending: TokenBudgetEntry[]
}

export interface RunCommandOptions {
  dryRun: boolean
}

export interface ManagerOsApiClient {
  getSystemStatus(): Promise<StatusCardData[]>
  getDailyOperatingLoop(date?: string): Promise<DailyOperatingLoop>
  getCommandRegistry(): Promise<CommandDefinition[]>
  getRecentRuns(): Promise<RunRecord[]>
  getTokenBudget(): Promise<TokenBudget>
  /**
   * Simulates queuing a command run. In this mock shell this NEVER makes a
   * network call or executes anything real — it only fabricates a RunRecord
   * for local UI state (e.g. Recent Runs).
   */
  runCommand(commandId: string, opts: RunCommandOptions): Promise<RunRecord>
}
