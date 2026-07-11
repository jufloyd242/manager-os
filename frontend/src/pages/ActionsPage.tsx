import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { ActionInbox } from '../components/ActionInbox'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { getDaily } from '../api/client'
import type { DailyOperatingLoop, RunRecord } from '../api/client'

interface ActionsPageProps {
  onRunRecorded: (run: RunRecord) => void
}

export function ActionsPage({ onRunRecorded }: ActionsPageProps) {
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [priorityFilter, setPriorityFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<string>('all')

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

  const allActions = loop?.recommended_actions || []
  const sources = [...new Set(allActions.map(a => a.source).filter(Boolean))] as string[]

  const filtered = allActions.filter(a => {
    if (search && !a.title.toLowerCase().includes(search.toLowerCase()) && !a.reason.toLowerCase().includes(search.toLowerCase())) return false
    if (priorityFilter !== 'all' && a.priority !== priorityFilter) return false
    if (sourceFilter !== 'all' && a.source !== sourceFilter) return false
    return true
  })

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Actions" description="What work is waiting for me?" />
      <div className="flex items-center gap-3 px-6 py-2 border-b border-slate-200 bg-white shrink-0">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search actions..."
          className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
        />
        <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="all">All priorities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="all">All sources</option>
          {sources.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        {filtered.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-8">No actions match your filters.</p>
        ) : (
          <ActionInbox
            actionSummary={loop?.action_summary}
            actionGroups={loop?.action_groups}
            recommendedActions={filtered}
            onRunRecorded={onRunRecorded}
          />
        )}
      </div>
    </div>
  )
}
