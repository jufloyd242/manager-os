import { useEffect, useState } from 'react'
import { Layout } from './components/Layout'
import { StatusCard } from './components/StatusCard'
import { DailySection } from './components/DailySection'
import { ActionInbox } from './components/ActionInbox'
import { CommandCenter } from './components/CommandCenter'
import { RecentRuns } from './components/RecentRuns'
import { TokenBudgetPanel } from './components/TokenBudgetPanel'
import { getStatus, getDaily, runSafeRefresh } from './api/client'
import { RecommendedActionCard } from './components/RecommendedActionCard'
import type { StatusCardData, DailyOperatingLoop, RunRecord, TokenEstimate } from './api/client'

function App() {
  const [activeView, setActiveView] = useState<'daily_loop' | 'staffing' | 'meetings' | 'projects' | 'archive'>('daily_loop')
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [statusMock, setStatusMock] = useState(false)
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [loopMock, setLoopMock] = useState(false)
  const [estimate, setEstimate] = useState<TokenEstimate | null>(null)
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
  const [toast, setToast] = useState<string | null>(null)
  const [apiError, setApiError] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefreshTime, setLastRefreshTime] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const loadAllData = () => {
    return Promise.all([getStatus(), getDaily()])
      .then(([statusRes, dailyRes]) => {
        setStatus(statusRes.data)
        setStatusMock(statusRes.isMock)
        setLoop(dailyRes.data)
        setLoopMock(dailyRes.isMock)
        if (!lastRefreshTime) {
          setLastRefreshTime(new Date().toLocaleTimeString())
        }
        setApiError(false)
      })
      .catch((err) => {
        console.error('API Load Error:', err)
        setApiError(true)
      })
  }

  useEffect(() => {
    loadAllData()
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  const handleSafeRefresh = async () => {
    setRefreshing(true)
    setToast('Starting safe local refresh...')
    try {
      await runSafeRefresh()
      setLastRefreshTime(new Date().toLocaleTimeString())
      await loadAllData()
      setToast('Manager OS successfully refreshed!')
    } catch (err) {
      console.error(err)
      setApiError(true)
    } finally {
      setRefreshing(false)
    }
  }

  function handleRunRecorded(run: RunRecord) {
    setRunsRefreshKey((key) => key + 1)
    setToast(`Run ${run.status} for "${run.command_id}".`)
  }

  const usingMockData = statusMock || loopMock

  if (apiError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 p-6 font-sans">
        <div className="max-w-md w-full bg-white rounded-2xl border border-slate-200 p-8 shadow-sm text-center space-y-6">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 animate-pulse">
            <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <h2 className="text-xl font-extrabold text-slate-900">Backend Unreachable</h2>
            <p className="mt-2 text-sm text-slate-500 leading-relaxed">
              The Manager OS local-first API is currently offline. Please ensure the local backend server is running and accessible at the address below.
            </p>
          </div>
          <div className="rounded-lg bg-slate-50 p-3.5 text-xs text-slate-600 font-mono text-left space-y-2">
            <div>
              <span className="font-bold text-slate-500">API Address:</span> http://127.0.0.1:8000
            </div>
            <div>
              <span className="font-bold text-slate-500">Start Command:</span>
              <code className="mt-1 block bg-slate-900 text-slate-100 p-2 rounded border border-slate-800 font-bold">
                fastapi dev src/manager_os/api/app.py
              </code>
            </div>
          </div>
          <div className="flex gap-3 justify-center pt-2">
            <button
              onClick={loadAllData}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2.5 px-4 rounded-xl text-sm transition-colors cursor-pointer shadow-sm"
            >
              Retry Connection
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Get stale sources for warning banner
  const staleSources = status.filter((s) => s.freshness === 'stale')

  const badges = {
    daily_loop: loop?.recommended_actions?.length ?? 0,
    staffing: loop?.people_staffing?.length ?? 0,
    archive: 0
  }

  return (
    <Layout
      currentView={activeView}
      onViewChange={setActiveView}
      badges={badges}
    >
      {toast && (
        <div className="mb-4 rounded-lg border border-slate-200 bg-slate-900 px-4 py-2.5 text-sm text-white shadow-md animate-fade-in">
          {toast}
        </div>
      )}

      {usingMockData && (
        <div
          data-testid="dashboard-mock-indicator"
          className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm font-medium text-amber-800"
        >
          Offline / Mock Data — the Manager OS API is unreachable, showing local mock data instead.
        </div>
      )}

      {/* TODAY / HOME VIEW */}
      {activeView === 'daily_loop' && (
        <div className="space-y-8">
          {/* SECTION 1: HEADER */}
          <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Today's Operating Dashboard</h2>
              <p className="text-xs text-slate-500 mt-1">
                Last successful refresh: <span className="font-semibold text-slate-700">{lastRefreshTime ?? 'Never'}</span>
              </p>
              
              {/* Concise stale-source warning banner */}
              {staleSources.length > 0 && (
                <div className="mt-3 flex items-center gap-2 text-xs text-amber-700 bg-amber-50 px-3 py-1.5 rounded-lg border border-amber-200/60 max-w-fit">
                  <svg className="h-4 w-4 text-amber-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <span className="font-medium">
                    Stale data sources detected: {staleSources.map((s) => s.label).join(', ')}
                  </span>
                </div>
              )}
            </div>

            <button
              onClick={handleSafeRefresh}
              disabled={refreshing}
              className={`px-4 py-2.5 rounded-xl font-bold text-sm text-white shadow-sm flex items-center gap-2 transition-all cursor-pointer ${
                refreshing ? 'bg-slate-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 shadow-indigo-500/10'
              }`}
            >
              {refreshing ? (
                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              ) : (
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H17" />
                </svg>
              )}
              {refreshing ? 'Refreshing...' : 'Refresh Manager OS'}
            </button>
          </div>

          {/* SECTION 2: NEEDS YOUR ATTENTION */}
          <section aria-label="Needs Your Attention" className="space-y-4">
            <div className="flex items-baseline justify-between border-b border-slate-100 pb-2">
              <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400">Needs Your Attention (Max 5)</h2>
              <span className="text-xs text-indigo-500 font-bold">{loop?.recommended_actions?.length ?? 0} urgent tasks</span>
            </div>
            
            {loop?.recommended_actions && loop.recommended_actions.length > 0 ? (
              <div className="grid grid-cols-1 gap-4">
                {loop.recommended_actions.map((action) => (
                  <RecommendedActionCard
                    key={action.id ?? action.title}
                    action={action}
                    onRunRecorded={handleRunRecorded}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-400 shadow-sm">
                No attention items found. Operating state is fully aligned!
              </div>
            )}
          </section>

          {/* SECTION 3: MEETINGS */}
          <section aria-label="Meetings" className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400 border-b border-slate-100 pb-2">Meetings Needing Prep (Today/Tomorrow)</h2>
            {loop?.meetings && loop.meetings.length > 0 ? (
              <div className="bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100 shadow-sm overflow-hidden">
                {loop.meetings.map((m: any) => (
                  <div key={m.id || m.title} className="p-4 flex items-center justify-between hover:bg-slate-50/50 transition-colors">
                    <div>
                      <h4 className="text-sm font-bold text-slate-800">{m.title}</h4>
                      <p className="text-xs text-rose-500 font-semibold mt-1">Required: {m.reason}</p>
                    </div>
                    {m.start_time && (
                      <span className="text-xs font-semibold bg-slate-100 text-slate-600 px-2.5 py-1 rounded-md">
                        {m.start_time}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-400 shadow-sm">
                No meetings require immediate preparation.
              </div>
            )}
          </section>

          {/* SECTION 4: STAFFING EXCEPTIONS */}
          <section aria-label="Staffing Exceptions" className="space-y-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400 border-b border-slate-100 pb-2">Staffing Exceptions</h2>
            {loop?.people_staffing && loop.people_staffing.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {loop.people_staffing.map((p: any) => (
                  <div key={p.person_name} className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex items-start justify-between">
                    <div className="space-y-1">
                      <h4 className="text-sm font-bold text-slate-800">{p.person_name}</h4>
                      <p className="text-xs text-slate-500">Allocation: <span className="font-bold text-slate-700">{p.allocation_pct}%</span></p>
                      <p className="text-xs text-rose-600 font-semibold">{p.warning}</p>
                    </div>
                    <span className="text-xs font-bold text-rose-600 bg-rose-50 px-2 py-0.5 rounded-full border border-rose-100">
                      Exception
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-400 shadow-sm">
                No staffing capacity exceptions. Teams are balanced.
              </div>
            )}
          </section>

          {/* SECTION 5: ADVANCED SECTION (Collapsed by default) */}
          <section aria-label="Advanced Details" className="pt-4 border-t border-slate-200">
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-2 text-sm font-bold text-slate-500 hover:text-slate-800 transition-colors uppercase tracking-wider cursor-pointer"
            >
              <svg
                className={`w-5 h-5 transition-transform duration-200 ${showAdvanced ? 'rotate-90' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7" />
              </svg>
              Advanced Operations Console
            </button>

            {showAdvanced && (
              <div className="mt-6 space-y-8 animate-fade-in">
                {/* Source details */}
                <div className="space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Data Sources &amp; Freshness Details</h3>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
                    {status.map((card) => (
                      <StatusCard key={card.id} data={card} />
                    ))}
                  </div>
                </div>

                {/* Command Center */}
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                  <div className="xl:col-span-2">
                    <CommandCenter onRunRecorded={handleRunRecorded} onEstimate={setEstimate} />
                  </div>
                  <div className="space-y-4">
                    <TokenBudgetPanel estimate={estimate} />
                    <RecentRuns refreshKey={runsRefreshKey} />
                  </div>
                </div>

                {/* Document Gaps */}
                {loop?.document_gaps && loop.document_gaps.length > 0 && (
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Document Gaps</h3>
                    <div className="bg-white border border-slate-200 rounded-xl p-4 divide-y divide-slate-100 shadow-sm">
                      {loop.document_gaps.map((g: any) => (
                        <div key={g.opportunity_number} className="py-2 first:pt-0 last:pb-0 text-xs text-slate-600 flex justify-between items-center">
                          <span>{g.opportunity_number} — <span className="font-semibold text-slate-800">{g.project_name}</span> ({g.client})</span>
                          <code className="bg-slate-50 border border-slate-100 px-2 py-0.5 rounded font-mono text-[10px] text-slate-500">
                            {g.suggested_command}
                          </code>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Feedback Learning */}
                {loop?.feedback_learning && loop.feedback_learning.length > 0 && (
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Feedback Patterns Learned</h3>
                    <div className="bg-white border border-slate-200 rounded-xl p-4 divide-y divide-slate-100 shadow-sm">
                      {loop.feedback_learning.map((f: any) => (
                        <div key={f.pattern_type} className="py-2.5 first:pt-0 last:pb-0 text-xs text-slate-600 flex justify-between items-center">
                          <div>
                            <span className="font-bold text-slate-800">{f.pattern_type}</span> on {f.entity_name} ({f.rating}, x{f.event_count})
                          </div>
                          <span className="italic text-[11px] text-slate-500">Suggested Action: {f.suggested_action}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      {/* MEETINGS VIEW */}
      {activeView === 'meetings' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-slate-900">Meetings Calendar &amp; Preparation</h2>
            <p className="text-xs text-slate-500 mt-1">Review upcoming meetings requiring context synthesis or direct actions</p>
          </div>
          {loop?.meetings && loop.meetings.length > 0 ? (
            <div className="bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100 shadow-sm overflow-hidden">
              {loop.meetings.map((m: any) => (
                <div key={m.id || m.title} className="p-4 flex items-center justify-between hover:bg-slate-50/50 transition-colors">
                  <div>
                    <h4 className="text-sm font-bold text-slate-800">{m.title}</h4>
                    <p className="text-xs text-slate-500 mt-1">Meeting Date: {m.meeting_date || loop.date}</p>
                    <p className="text-xs text-rose-500 font-semibold mt-1">Required Action: {m.reason}</p>
                  </div>
                  {m.start_time && (
                    <span className="text-xs font-semibold bg-slate-100 text-slate-600 px-2.5 py-1 rounded-md">
                      {m.start_time}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-400 shadow-sm">
              No meetings require immediate preparation.
            </div>
          )}
        </div>
      )}

      {/* PROJECTS VIEW */}
      {activeView === 'projects' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-slate-900">Active Engagements &amp; Deals</h2>
            <p className="text-xs text-slate-500 mt-1">Review live projects and closing deals requiring manager oversight</p>
          </div>
          {loop?.projects_deals && loop.projects_deals.length > 0 ? (
            <div className="bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100 shadow-sm overflow-hidden">
              {loop.projects_deals.map((d: any) => (
                <div key={d.entity_name} className="p-4 flex items-start justify-between hover:bg-slate-50/50 transition-colors">
                  <div>
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-bold text-slate-800">{d.entity_name}</h4>
                      <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase ${
                        d.severity === 'high' ? 'bg-red-50 text-red-700 border border-red-100' : 'bg-amber-50 text-amber-700 border border-amber-100'
                      }`}>
                        {d.severity} Risk
                      </span>
                    </div>
                    <p className="text-xs text-slate-600 mt-1.5 font-medium">{d.summary}</p>
                    {d.why_it_matters && (
                      <p className="text-xs text-slate-400 italic mt-1">Why it matters: {d.why_it_matters}</p>
                    )}
                  </div>
                  <span className="text-xs font-semibold text-slate-500 bg-slate-100 px-2.5 py-1 rounded-md capitalize">
                    {d.entity_type}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-400 shadow-sm">
              No project or deal risk signals open.
            </div>
          )}
        </div>
      )}

      {/* STAFFING VIEW */}
      {activeView === 'staffing' && loop && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-slate-900">People &amp; Capacity Dashboard</h2>
            <p className="text-xs text-slate-500 mt-1">Interactive operational state for resource planning and optimization</p>
          </div>

          <div className="max-w-xl">
            <DailySection title="People / Staffing" items={loop.people_staffing} />
          </div>
        </div>
      )}

      {/* ARCHIVE VIEW */}
      {activeView === 'archive' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-slate-900">Historical Archive</h2>
            <p className="text-xs text-slate-500 mt-1">Review de-scoped opportunities, legacy clients, and inactive repositories</p>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-4">
              <h3 className="text-base font-bold text-slate-900">Project Archive</h3>
              <p className="text-xs text-slate-500 mt-1">Historical engagements and archived projects marked as LEGACY_EMPTY or ARCHIVED.</p>
            </div>
            
            {loop?.projects_deals && loop.projects_deals.some(p => p.document_status === 'LEGACY_EMPTY' || p.status === 'ARCHIVED') ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs text-slate-600">
                  <thead className="bg-slate-50 text-slate-400 uppercase tracking-wider font-bold">
                    <tr>
                      <th className="px-4 py-3">Client</th>
                      <th className="px-4 py-3">Project</th>
                      <th className="px-4 py-3">Opportunity #</th>
                      <th className="px-4 py-3">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {loop.projects_deals.filter(p => p.document_status === 'LEGACY_EMPTY' || p.status === 'ARCHIVED').map((p: any) => (
                      <tr key={p.id} className="hover:bg-slate-50/50 transition-colors">
                        <td className="px-4 py-3.5 font-medium text-slate-900">{p.client}</td>
                        <td className="px-4 py-3.5">{p.name}</td>
                        <td className="px-4 py-3.5 font-mono">{p.opportunity_number}</td>
                        <td className="px-4 py-3.5">
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                            p.document_status === 'LEGACY_EMPTY' ? 'bg-amber-50 text-amber-700 border border-amber-200' : 'bg-slate-100 text-slate-600'
                          }`}>
                            {p.document_status || p.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-slate-400 text-center py-6">No archived engagements or de-scoped opportunities found in the current dataset.</p>
            )}
          </div>
        </div>
      )}
    </Layout>
  )
}

export default App
