import { useMemo, useState } from 'react'
import type { ActionGroup, ActionSummary, RecommendedAction, RunRecord } from '../api/client'
import { RecommendedActionCard } from './RecommendedActionCard'

export interface ActionInboxProps {
  /** Optional — when present, drives the header counts directly. When
   * absent, a summary is computed client-side from whatever groups end up
   * rendering (either `actionGroups` or the client-side fallback grouping). */
  actionSummary?: ActionSummary
  /** Optional — when present (and non-empty), rendered as-is. When absent,
   * `recommendedActions` is grouped client-side by `source` so the UI
   * degrades gracefully against an older backend. */
  actionGroups?: ActionGroup[]
  recommendedActions: RecommendedAction[]
  onRunRecorded?: (run: RunRecord) => void
}

function humanizeSource(source: string): string {
  return source
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

// Mirrors `_is_executable` in `manager_os.build.daily_action_groups`: an
// action counts as executable if it has a primary_command OR any
// secondary_commands, not just primary_command alone.
function isExecutable(action: RecommendedAction): boolean {
  return Boolean(action.primary_command) || Boolean(action.secondary_commands && action.secondary_commands.length > 0)
}

function summarizeActions(actions: RecommendedAction[]): ActionSummary {
  const by_source: Record<string, number> = {}
  const by_priority = { high: 0, medium: 0, low: 0 }
  let executable = 0
  let informational = 0
  for (const action of actions) {
    const source = action.source ?? 'other'
    by_source[source] = (by_source[source] ?? 0) + 1
    by_priority[action.priority] += 1
    if (isExecutable(action)) executable += 1
    else informational += 1
  }
  return { total: actions.length, by_source, by_priority, executable, informational }
}

/** Fallback grouping used when the backend doesn't (yet) send
 * `action_groups` — buckets the flat `recommended_actions` list by their
 * `source` field (or "other" when missing). */
function groupActionsBySource(actions: RecommendedAction[]): ActionGroup[] {
  const buckets = new Map<string, RecommendedAction[]>()
  for (const action of actions) {
    const source = action.source ?? 'other'
    const bucket = buckets.get(source) ?? []
    bucket.push(action)
    buckets.set(source, bucket)
  }
  return Array.from(buckets.entries()).map(([source, groupActions]) => {
    const priorities = groupActions.map((a) => a.priority)
    const priority = priorities.includes('high') ? 'high' : priorities.includes('medium') ? 'medium' : 'low'
    return {
      id: source,
      title: humanizeSource(source),
      source,
      count: groupActions.length,
      priority,
      summary: `${groupActions.length} action${groupActions.length === 1 ? '' : 's'}`,
      default_visible_count: Math.min(5, groupActions.length),
      actions: groupActions,
    }
  })
}

function matchesSearch(action: RecommendedAction, term: string): boolean {
  const haystack = `${action.entity_id ?? ''} ${action.title} ${action.reason}`.toLowerCase()
  return haystack.includes(term.toLowerCase())
}

interface ActionGroupCardProps {
  group: ActionGroup
  onRunRecorded?: (run: RunRecord) => void
}

function ActionGroupCard({ group, onRunRecorded }: ActionGroupCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [search, setSearch] = useState('')
  const isDocumentGaps = group.source === 'document_gaps'

  const filteredActions = useMemo(() => {
    if (!isDocumentGaps || !search.trim()) return group.actions
    return group.actions.filter((action) => matchesSearch(action, search))
  }, [group.actions, isDocumentGaps, search])

  const visibleActions = expanded ? filteredActions : filteredActions.slice(0, group.default_visible_count)
  const canShowAll = !expanded && filteredActions.length > group.default_visible_count
  const canShowLess = expanded && filteredActions.length > group.default_visible_count

  return (
    <div
      data-testid={`action-group-${group.id}`}
      className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-700">{group.title}</h3>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-500">
          {group.count}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{group.summary}</p>

      {isDocumentGaps && (
        <input
          type="text"
          aria-label="Search document gaps"
          placeholder="Search by OppID, client, or project"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="mt-2 w-full rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-700"
        />
      )}

      {filteredActions.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">No matching actions.</p>
      ) : (
        <div className="mt-2 space-y-2">
          {visibleActions.map((action) => (
            <div data-testid="action-item" key={action.id ?? action.title}>
              <RecommendedActionCard action={action} onRunRecorded={onRunRecorded} />
            </div>
          ))}
        </div>
      )}

      {(canShowAll || canShowLess) && (
        <div className="mt-2 flex gap-3">
          {canShowAll && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="text-xs font-medium text-blue-600 hover:underline"
            >
              Show all ({filteredActions.length})
            </button>
          )}
          {canShowLess && (
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="text-xs font-medium text-slate-500 hover:underline"
            >
              Show less
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Grouped, scalable replacement for the old flat recommended-actions list.
 * Renders a summary header (total / by source / by priority / executable vs
 * informational) followed by one collapsible card per group. Falls back to
 * client-side grouping of `recommendedActions` by `source` when
 * `actionGroups` isn't provided (older backend). Individual actions are
 * rendered via the existing `RecommendedActionCard` (Dry Run / Print Prompt /
 * guarded Run Live Fetch buttons unchanged). No batch-live or raw
 * shell-command button is ever rendered here.
 */
export function ActionInbox({
  actionSummary,
  actionGroups,
  recommendedActions,
  onRunRecorded,
}: ActionInboxProps) {
  const groups = useMemo<ActionGroup[]>(
    () => (actionGroups && actionGroups.length > 0 ? actionGroups : groupActionsBySource(recommendedActions)),
    [actionGroups, recommendedActions],
  )
  const fallbackSummary = useMemo(() => summarizeActions(groups.flatMap((g) => g.actions)), [groups])
  const summary = actionSummary ?? fallbackSummary

  return (
    <div data-testid="action-inbox" className="space-y-4">
      <div data-testid="action-inbox-header" className="rounded-xl border border-slate-200 bg-slate-50 p-4">
        <p className="text-sm font-semibold text-slate-700">{summary.total} total actions</p>
        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
          {Object.entries(summary.by_source).map(([source, count]) => (
            <span key={source} className="rounded-full bg-white px-2 py-0.5 shadow-sm">
              {humanizeSource(source)}: {count}
            </span>
          ))}
        </div>
        <div className="mt-1 text-xs text-slate-500">
          High: {summary.by_priority.high} · Medium: {summary.by_priority.medium} · Low: {summary.by_priority.low}
        </div>
        <div className="mt-1 text-xs text-slate-500">
          Executable: {summary.executable} · Informational: {summary.informational}
        </div>
      </div>

      <div className="space-y-3">
        {groups.map((group) => (
          <ActionGroupCard key={group.id} group={group} onRunRecorded={onRunRecorded} />
        ))}
      </div>
    </div>
  )
}
