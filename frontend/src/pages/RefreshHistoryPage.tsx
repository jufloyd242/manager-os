import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { EmptyState } from '../components/primitives/EmptyState'
import { StatusBadge } from '../components/primitives/StatusBadge'
import { getRuns } from '../api/client'
import type { RunRecord } from '../api/client'

export function RefreshHistoryPage() {
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getRuns(50)
      setRuns(result.data)
    } catch {
      setError('Failed to load refresh history')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Refresh History" description="What happened during recent refreshes?" />
      <div className="flex-1 overflow-y-auto p-6">
        {runs.length === 0 ? (
          <EmptyState message="No refresh history available yet. Run a refresh to see results here." />
        ) : (
          <div className="space-y-2">
            {runs.map(run => (
              <div key={run.run_id} className="flex items-center gap-3 px-4 py-3 rounded-lg border border-slate-200 bg-white">
                <StatusBadge status={run.status} />
                <span className="text-sm font-medium text-slate-900">{run.command_id}</span>
                <span className="text-xs text-slate-400">{run.started_at}</span>
                {run.finished_at && <span className="text-xs text-slate-400">→ {run.finished_at}</span>}
                {run.dry_run && <span className="text-[10px] text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">DRY RUN</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
