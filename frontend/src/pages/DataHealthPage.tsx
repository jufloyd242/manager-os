import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { EmptyState } from '../components/primitives/EmptyState'
import { StatusBadge } from '../components/primitives/StatusBadge'
import { getStatus, postRefresh } from '../api/client'
import type { StatusCardData } from '../api/client'

export function DataHealthPage() {
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedSource, setSelectedSource] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getStatus()
      setStatus(result.data)
    } catch {
      setError('Failed to load data health')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshResult(null)
    try {
      await postRefresh({ sources: ['obsidian', 'deals', 'forecast', 'summary'], run_extraction: true })
      await load()
      setRefreshResult('Refresh complete')
    } catch {
      setRefreshResult('Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  const selected = status.find(s => s.id === selectedSource)

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Data Health"
        description="Can I trust the data powering the dashboard?"
        primaryAction={
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
          >
            {refreshing ? 'Refreshing...' : 'Refresh Data'}
          </button>
        }
      />
      {refreshResult && (
        <div className="px-6 py-1.5 text-xs text-slate-500 bg-slate-50 border-b border-slate-200">
          {refreshResult}
        </div>
      )}
      <div className="flex-1 flex overflow-hidden">
        {/* Source list */}
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {status.length === 0 ? (
            <EmptyState message="No data sources found." />
          ) : (
            status.map(s => (
              <button
                key={s.id}
                onClick={() => setSelectedSource(s.id)}
                className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${
                  selectedSource === s.id ? 'bg-indigo-50' : ''
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-900">{s.label}</span>
                  <StatusBadge status={s.freshness} />
                </div>
                <p className="text-xs text-slate-500 mt-0.5">{s.detail}</p>
                {typeof s.count === 'number' && <p className="text-xs text-slate-400">{s.count} rows</p>}
              </button>
            ))
          )}
        </div>

        {/* Detail */}
        <div className="w-1/2 overflow-y-auto p-6">
          {!selected ? (
            <EmptyState message="Select a data source to see details." />
          ) : (
            <div className="space-y-3">
              <h2 className="text-lg font-bold text-slate-900">{selected.label}</h2>
              <dl className="text-sm space-y-2">
                <div>
                  <dt className="text-slate-400 text-xs">Freshness</dt>
                  <dd><StatusBadge status={selected.freshness} /></dd>
                </div>
                <div>
                  <dt className="text-slate-400 text-xs">Detail</dt>
                  <dd className="text-slate-700">{selected.detail}</dd>
                </div>
                <div>
                  <dt className="text-slate-400 text-xs">Record Count</dt>
                  <dd className="text-slate-700">{selected.count ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-slate-400 text-xs">Source ID</dt>
                  <dd className="text-slate-700 font-mono text-xs">{selected.id}</dd>
                </div>
              </dl>

              {/* Safe local refresh */}
              <div className="border-t border-slate-100 pt-3">
                <button
                  onClick={handleRefresh}
                  disabled={refreshing}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
                >
                  {refreshing ? 'Refreshing...' : 'Refresh this source'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
