import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { SummaryStrip } from '../components/primitives/SummaryStrip'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { RecommendedActionCard } from '../components/RecommendedActionCard'
import { getDaily, getStatus, postRefresh } from '../api/client'
import type { DailyOperatingLoop, StatusCardData, RunRecord } from '../api/client'
import type { Route } from '../hooks/useHashRoute'

interface TodayPageProps {
  onNavigate: (route: Route) => void
  onRunRecorded: (run: RunRecord) => void
}

export function TodayPage({ onNavigate, onRunRecorded }: TodayPageProps) {
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [dailyResult, statusResult] = await Promise.all([
        getDaily(),
        getStatus(),
      ])
      setLoop(dailyResult.data)
      setStatus(statusResult.data)
    } catch {
      setError('Failed to load dashboard data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshResult(null)
    try {
      const result = await postRefresh({ sources: ['obsidian', 'deals', 'forecast', 'summary'], run_extraction: true })
      if (result.data.ok) {
        setRefreshResult('Refresh complete')
        await load()
      } else {
        setRefreshResult('Refresh completed with warnings')
      }
    } catch {
      setRefreshResult('Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
  const topActions = (loop?.recommended_actions || []).slice(0, 5)
  const nextMeetings = (loop?.meetings || []).slice(0, 4)

  const dataFresh = status.find(s => s.id === 'obsidian')?.freshness || 'unknown'

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title={today}
        description="Here's what needs your attention."
        freshness={`Last refreshed: ${dataFresh}`}
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
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {/* Summary strip */}
        <SummaryStrip
          items={[
            { label: 'Meetings', value: loop?.meetings.length ?? 0, status: 'today', onClick: () => onNavigate('meetings') },
            { label: 'Deals', value: loop?.recommended_actions.filter(a => a.source === 'projects_deals').length ?? 0, status: 'urgent', onClick: () => onNavigate('deals') },
            { label: 'Staffing', value: loop?.people_staffing.length ?? 0, status: 'exceptions', onClick: () => onNavigate('forecast') },
            { label: 'Data', value: dataFresh, status: 'health', onClick: () => onNavigate('data-health') },
          ]}
        />

        {/* Top actions */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-slate-700">Top Actions</h2>
            <button
              onClick={() => onNavigate('actions')}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 cursor-pointer"
            >
              View all actions
            </button>
          </div>
          <div className="space-y-2">
            {topActions.length === 0 ? (
              <p className="text-sm text-slate-400">No actions need attention.</p>
            ) : (
              topActions.map((action, i) => (
                <div key={action.id || i} className="flex items-start gap-3">
                  <span className="text-xs font-bold text-slate-400 mt-1 w-4">{i + 1}</span>
                  <div className="flex-1">
                    <RecommendedActionCard action={action} onRunRecorded={onRunRecorded} />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Next meetings */}
        <div>
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Next Meetings</h2>
          <div className="space-y-1">
            {nextMeetings.length === 0 ? (
              <p className="text-sm text-slate-400">No meetings scheduled.</p>
            ) : (
              nextMeetings.map((m, i) => {
                const meeting = m as Record<string, unknown>
                return (
                  <button
                    key={i}
                    onClick={() => onNavigate('meetings')}
                    className="w-full flex items-center gap-3 px-3 py-2 rounded-lg border border-slate-200 bg-white hover:shadow-sm transition-shadow text-left cursor-pointer"
                  >
                    <span className="text-xs font-mono text-slate-500 w-16">
                      {String(meeting.time || meeting.start_time || '')}
                    </span>
                    <span className="text-sm font-medium text-slate-800 truncate">
                      {String(meeting.title || 'Untitled')}
                    </span>
                    {meeting.needs_prep as boolean && (
                      <span className="text-[10px] font-bold text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">PREP</span>
                    )}
                  </button>
                )
              })
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
