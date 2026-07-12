import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { EmptyState } from '../components/primitives/EmptyState'
import { getDaily } from '../api/client'
import type { DailyOperatingLoop, RunRecord, RecommendedAction } from '../api/client'

interface ActionsPageProps {
  onRunRecorded: (run: RunRecord) => void
}

const PRIORITY_ORDER: Record<string, number> = {
  high: 0,
  medium: 1,
  low: 2,
}

export function ActionsPage({ onRunRecorded: _onRunRecorded }: ActionsPageProps) {
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [priorityFilter, setPriorityFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<string>('all')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getDaily()
      setLoop(result.data)
    } catch {
      setError('Failed to load actions')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const allActions: RecommendedAction[] = loop?.recommended_actions || []
  const sources = [...new Set(allActions.map(a => a.source).filter(Boolean))] as string[]
  const types = [...new Set(allActions.map(a => a.entity_type).filter(Boolean))] as string[]

  const filtered = allActions
    .filter(a => {
      if (search && !a.title.toLowerCase().includes(search.toLowerCase()) && !a.reason.toLowerCase().includes(search.toLowerCase())) return false
      if (priorityFilter !== 'all' && a.priority !== priorityFilter) return false
      if (sourceFilter !== 'all' && a.source !== sourceFilter) return false
      if (typeFilter !== 'all' && a.entity_type !== typeFilter) return false
      return true
    })
    .sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3))

  const selectedAction = selectedActionId
    ? allActions.find(a => (a.id || a.title) === selectedActionId)
    : null

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Actions" description="What work is waiting for me?" />
      {/* Toolbar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-slate-200 bg-white flex-wrap">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search actions..."
          className="flex-1 min-w-[120px] rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
        />
        <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="all">All priorities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="all">All sources</option>
          {sources.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="all">All types</option>
          {types.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      {/* Master-detail */}
      <div className="flex-1 flex overflow-hidden">
        {/* Ranked list */}
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {filtered.length === 0 ? (
            <EmptyState message="No actions match your filters." />
          ) : (
            <div className="divide-y divide-slate-100">
              {filtered.map((action, i) => {
                const actionKey = action.id || action.title
                const isSelected = selectedActionId === actionKey
                return (
                  <button
                    key={actionKey}
                    onClick={() => setSelectedActionId(isSelected ? null : actionKey)}
                    className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors cursor-pointer ${
                      isSelected ? 'bg-indigo-50 border-l-4 border-l-indigo-500' : ''
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-400 w-5 shrink-0">{i + 1}</span>
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0 ${
                        action.priority === 'high' ? 'bg-red-100 text-red-700' :
                        action.priority === 'medium' ? 'bg-amber-100 text-amber-700' :
                        'bg-slate-100 text-slate-600'
                      }`}>
                        {action.priority}
                      </span>
                      <p className="text-sm font-medium text-slate-900 truncate flex-1">{action.title}</p>
                    </div>
                    {action.reason && (
                      <p className="text-xs text-slate-500 truncate mt-1 ml-7">{action.reason}</p>
                    )}
                    <div className="flex items-center gap-3 mt-1 ml-7 text-xs text-slate-400">
                      {action.source && <span>{action.source}</span>}
                      {action.entity_type && <span>· {action.entity_type}</span>}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Detail */}
        <div className="w-1/2 overflow-y-auto p-6">
          {!selectedAction ? (
            <EmptyState message="Select an action to see details." />
          ) : (
            <div className="space-y-4">
              <div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                    selectedAction.priority === 'high' ? 'bg-red-100 text-red-700' :
                    selectedAction.priority === 'medium' ? 'bg-amber-100 text-amber-700' :
                    'bg-slate-100 text-slate-600'
                  }`}>
                    {selectedAction.priority}
                  </span>
                  <h2 className="text-lg font-bold text-slate-900">{selectedAction.title}</h2>
                </div>
              </div>

              {selectedAction.reason && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-1">Reason</h3>
                  <p className="text-sm text-slate-600">{selectedAction.reason}</p>
                </div>
              )}

              <dl className="text-sm space-y-1.5">
                {selectedAction.entity_type && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Entity Type</dt>
                    <dd className="text-slate-700">{selectedAction.entity_type}</dd>
                  </div>
                )}
                {selectedAction.entity_id && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Entity</dt>
                    <dd className="text-slate-700 font-mono text-xs">{selectedAction.entity_id}</dd>
                  </div>
                )}
                {selectedAction.source && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Source</dt>
                    <dd className="text-slate-700">{selectedAction.source}</dd>
                  </div>
                )}
                {selectedAction.command && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Command</dt>
                    <dd className="text-slate-700 font-mono text-xs">{selectedAction.command}</dd>
                  </div>
                )}
              </dl>

              {/* Execution controls */}
              {selectedAction.primary_command && (
                <div className="border-t border-slate-100 pt-3">
                  <h3 className="text-xs font-semibold text-slate-700 mb-2">Actions</h3>
                  <div className="space-y-2">
                    <div className="text-xs text-slate-500">
                      Primary: <span className="font-mono">{selectedAction.primary_command.command_id}</span>
                    </div>
                    {selectedAction.secondary_commands && selectedAction.secondary_commands.length > 0 && (
                      <div className="space-y-1">
                        {selectedAction.secondary_commands.map((sc, i) => (
                          <div key={i} className="text-xs text-slate-500">
                            {sc.label}: <span className="font-mono">{sc.command_id}</span>
                            {sc.requires_confirmation && (
                              <span className="ml-2 text-amber-600">⚠ requires confirmation</span>
                            )}
                            {sc.requires_successful_dry_run && (
                              <span className="ml-2 text-slate-400">dry-run first</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Provenance */}
              <details className="border-t border-slate-100 pt-3">
                <summary className="text-xs font-medium text-slate-500 cursor-pointer hover:text-slate-700">
                  Provenance
                </summary>
                <div className="mt-2 space-y-1 text-xs text-slate-400">
                  <p>ID: {selectedAction.id || '—'}</p>
                  <p>Source: {selectedAction.source || '—'}</p>
                  <p>Entity: {selectedAction.entity_type || '—'} / {selectedAction.entity_id || '—'}</p>
                </div>
              </details>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
