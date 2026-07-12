import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { SummaryStrip } from '../components/primitives/SummaryStrip'
import { LoadingState } from '../components/primitives/LoadingState'
import { getDaily, getStatus, postRefresh } from '../api/client'
import type { DailyOperatingLoop, StatusCardData, RunRecord, RecommendedAction, RefreshResult } from '../api/client'
import type { Route } from '../hooks/useHashRoute'

interface TodayPageProps {
  onNavigate: (route: Route) => void
  onRunRecorded: (run: RunRecord) => void
}

interface MeetingItem {
  id?: string
  title?: string
  start_time?: string
  time?: string
  needs_prep?: boolean
}

interface RefreshDetail {
  sources_attempted: string[]
  sources_succeeded: string[]
  sources_skipped: string[]
  sources_failed: string[]
  warnings: string[]
  errors: string[]
  extraction_result: string
}

export function TodayPage({ onNavigate, onRunRecorded: _onRunRecorded }: TodayPageProps) {
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [loopLoading, setLoopLoading] = useState(true)
  const [statusLoading, setStatusLoading] = useState(true)
  const [loopError, setLoopError] = useState<string | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<string | null>(null)
  const [refreshDetail, setRefreshDetail] = useState<RefreshDetail | null>(null)

  const loadDaily = useCallback(async () => {
    setLoopLoading(true)
    setLoopError(null)
    try {
      const result = await getDaily()
      setLoop(result.data)
    } catch {
      setLoopError('Failed to load daily data')
    } finally {
      setLoopLoading(false)
    }
  }, [])

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    setStatusError(null)
    try {
      const result = await getStatus()
      setStatus(result.data)
    } catch {
      setStatusError('Failed to load status')
    } finally {
      setStatusLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDaily()
    loadStatus()
  }, [loadDaily, loadStatus])

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshResult(null)
    setRefreshDetail(null)
    try {
      const result = await postRefresh({ sources: ['obsidian', 'deals', 'forecast', 'summary'], run_extraction: true })
      const data: RefreshResult = result.data
      const sourceEntries = Object.entries(data.sources || {})
      const succeeded = sourceEntries.filter(([, v]) => v.status === 'success').map(([k]) => k)
      const failed = sourceEntries.filter(([, v]) => v.status === 'failed' || v.status === 'error').map(([k]) => k)
      const skipped = sourceEntries.filter(([, v]) => v.status === 'skipped').map(([k]) => k)
      const warnings = sourceEntries.flatMap(([, v]) => v.warnings || [])
      const errors = sourceEntries.filter(([, v]) => v.error).map(([, v]) => v.error || '')
      const extraction = data.extraction?.ok ? 'success' : 'skipped'

      const hasFailures = failed.length > 0 || errors.length > 0
      const statusMsg = hasFailures
        ? `Refresh completed with ${failed.length} failure(s)`
        : 'Refresh complete'

      setRefreshResult(statusMsg)
      setRefreshDetail({
        sources_attempted: sourceEntries.map(([k]) => k),
        sources_succeeded: succeeded,
        sources_skipped: skipped,
        sources_failed: failed,
        warnings,
        errors,
        extraction_result: extraction,
      })

      // Reload data after refresh
      await Promise.all([loadDaily(), loadStatus()])
    } catch {
      setRefreshResult('Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
  const topActions: RecommendedAction[] = (loop?.recommended_actions || []).slice(0, 5)
  const nextMeetings: MeetingItem[] = ((loop?.meetings || []) as unknown as MeetingItem[]).slice(0, 4)

  // Find actual last refresh time from status
  const lastRefresh = status.find(s => s.detail?.includes('Last updated'))?.detail
    || status.find(s => s.freshness === 'fresh')?.detail
    || 'Unknown'

  if (loopLoading && statusLoading) return <LoadingState />

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title={today}
        description="Here's what needs your attention."
        freshness={`Last refreshed: ${lastRefresh}`}
        primaryAction={
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
          >
            {refreshing ? 'Refreshing...' : 'Refresh Manager OS'}
          </button>
        }
      />

      {refreshResult && (
        <div className="px-6 py-1.5 text-xs text-slate-500 bg-slate-50 border-b border-slate-200">
          {refreshResult}
          {refreshDetail && (
            <details className="mt-1">
              <summary className="cursor-pointer text-slate-400 hover:text-slate-600">Details</summary>
              <div className="mt-1 space-y-1 text-xs">
                {refreshDetail.sources_attempted.length > 0 && (
                  <p>Attempted: {refreshDetail.sources_attempted.join(', ')}</p>
                )}
                {refreshDetail.sources_succeeded.length > 0 && (
                  <p className="text-green-600">Succeeded: {refreshDetail.sources_succeeded.join(', ')}</p>
                )}
                {refreshDetail.sources_skipped.length > 0 && (
                  <p className="text-slate-400">Skipped: {refreshDetail.sources_skipped.join(', ')}</p>
                )}
                {refreshDetail.sources_failed.length > 0 && (
                  <p className="text-red-600">Failed: {refreshDetail.sources_failed.join(', ')}</p>
                )}
                {refreshDetail.warnings.length > 0 && (
                  <p className="text-amber-600">Warnings: {refreshDetail.warnings.join('; ')}</p>
                )}
                {refreshDetail.errors.length > 0 && (
                  <p className="text-red-600">Errors: {refreshDetail.errors.join('; ')}</p>
                )}
                {refreshDetail.extraction_result && (
                  <p>Extraction: {refreshDetail.extraction_result}</p>
                )}
              </div>
            </details>
          )}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Summary strip */}
        <SummaryStrip
          items={[
            { label: 'Meetings', value: loop?.meetings.length ?? 0, status: 'today', onClick: () => onNavigate('meetings') },
            { label: 'Deals', value: loop?.recommended_actions.filter(a => a.source === 'projects_deals').length ?? 0, status: 'urgent', onClick: () => onNavigate('deals') },
            { label: 'Staffing', value: loop?.people_staffing.length ?? 0, status: 'exceptions', onClick: () => onNavigate('forecast') },
            { label: 'Data', value: status.find(s => s.id === 'obsidian')?.freshness || 'unknown', status: 'health', onClick: () => onNavigate('data-health') },
          ]}
        />

        {/* Compact actions */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-slate-700">Top Actions</h2>
            <button
              onClick={() => onNavigate('actions')}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 cursor-pointer"
            >
              View all →
            </button>
          </div>
          <div className="space-y-1">
            {topActions.length === 0 ? (
              <p className="text-sm text-slate-400 py-2">No actions need attention.</p>
            ) : (
              topActions.map((action, i) => (
                <button
                  key={action.id || i}
                  onClick={() => onNavigate('actions')}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 transition-colors text-left cursor-pointer"
                >
                  <span className="text-xs font-bold text-slate-400 w-5 shrink-0">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0 ${
                        action.priority === 'high' ? 'bg-red-100 text-red-700' :
                        action.priority === 'medium' ? 'bg-amber-100 text-amber-700' :
                        'bg-slate-100 text-slate-600'
                      }`}>
                        {action.priority}
                      </span>
                      <p className="text-sm font-medium text-slate-800 truncate">{action.title}</p>
                    </div>
                    {action.reason && (
                      <p className="text-xs text-slate-500 truncate mt-0.5">{action.reason}</p>
                    )}
                  </div>
                  {action.source && (
                    <span className="text-[10px] text-slate-400 shrink-0">{action.source}</span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>

        {/* Compact meetings */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-slate-700">Next Meetings</h2>
            <button
              onClick={() => onNavigate('meetings')}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 cursor-pointer"
            >
              View all →
            </button>
          </div>
          <div className="space-y-1">
            {nextMeetings.length === 0 ? (
              <p className="text-sm text-slate-400 py-2">No meetings scheduled.</p>
            ) : (
              nextMeetings.map((m, i) => (
                <button
                  key={m.id || i}
                  onClick={() => onNavigate('meetings')}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 transition-colors text-left cursor-pointer"
                >
                  <span className="text-xs font-mono text-slate-500 w-16 shrink-0">
                    {String(m.start_time || m.time || '')}
                  </span>
                  <span className="text-sm font-medium text-slate-800 truncate flex-1">
                    {String(m.title || 'Untitled')}
                  </span>
                  {m.needs_prep && (
                    <span className="text-[10px] font-bold text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded shrink-0">
                      PREP
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>

        {/* Errors */}
        {loopError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {loopError}
          </div>
        )}
        {statusError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {statusError}
          </div>
        )}
      </div>
    </div>
  )
}
