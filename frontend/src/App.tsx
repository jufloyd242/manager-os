import { useEffect, useState } from 'react'
import { Layout } from './components/Layout'
import { StatusCard } from './components/StatusCard'
import { DailySection } from './components/DailySection'
import { ActionInbox } from './components/ActionInbox'
import { CommandCenter } from './components/CommandCenter'
import { RecentRuns } from './components/RecentRuns'
import { TokenBudgetPanel } from './components/TokenBudgetPanel'
import { Sidebar } from './components/Sidebar'
import { MeetingsView } from './components/MeetingsView'
import { DealsView } from './features/deals/DealsView'
import { ForecastView } from './features/forecast/ForecastView'
import { getStatus, getDaily } from './api/client'
import type { StatusCardData, DailyOperatingLoop, RunRecord, TokenEstimate } from './api/client'
import type { ViewId } from './components/Sidebar'

function App() {
  const [currentView, setCurrentView] = useState<ViewId>('daily_loop')
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [backendAvailable, setBackendAvailable] = useState(true)
  const [estimate, setEstimate] = useState<TokenEstimate | null>(null)
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    getStatus()
      .then((result) => {
        setStatus(result.data)
        setBackendAvailable(true)
      })
      .catch(() => {
        setBackendAvailable(false)
      })
    getDaily()
      .then((result) => {
        setLoop(result.data)
      })
      .catch(() => {
        // Daily is non-critical for availability check
      })
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  function handleRunRecorded(run: RunRecord) {
    setRunsRefreshKey((key) => key + 1)
    setToast(`Run ${run.status} for "${run.command_id}".`)
  }

  if (!backendAvailable && currentView === 'daily_loop') {
    return (
      <div className="flex h-screen bg-slate-100">
        <Sidebar currentView={currentView} onViewChange={setCurrentView} />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-2xl mt-20 text-center">
            <h1 className="text-2xl font-bold text-slate-900 mb-4">Manager OS</h1>
            <div className="rounded-xl border border-amber-300 bg-amber-50 p-6">
              <p className="text-amber-800 font-medium mb-2">Backend is not available</p>
              <p className="text-amber-700 text-sm mb-4">
                The Manager OS API is unreachable at http://127.0.0.1:8000.
              </p>
              <p className="text-amber-700 text-sm mb-4">
                Make sure the backend is running:
              </p>
              <code className="block rounded bg-amber-100 px-4 py-2 text-sm font-mono text-amber-900 mb-4">
                ./manager-os start
              </code>
              <button
                onClick={() => window.location.reload()}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 cursor-pointer"
              >
                Retry
              </button>
            </div>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="flex h-screen bg-slate-100">
      <Sidebar currentView={currentView} onViewChange={setCurrentView} />
      <main className="flex-1 overflow-y-auto p-6">
        {toast && (
          <div className="mb-4 rounded-lg border border-slate-200 bg-slate-900 px-4 py-2 text-sm text-white shadow">
            {toast}
          </div>
        )}

        {currentView === 'daily_loop' && (
          <Layout>
            <section aria-label="System Status">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">System Status</h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {status.map((card) => (
                  <StatusCard key={card.id} data={card} />
                ))}
              </div>
            </section>

            {loop && (
              <>
                <section aria-label="Daily Summary" className="mt-6">
                  <div className="mb-2 flex items-baseline justify-between">
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
                      Daily Summary
                    </h2>
                    <span className="text-xs text-slate-400">{loop.date}</span>
                  </div>
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <div
                      onClick={() => setCurrentView('meetings')}
                      className="cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:shadow-md transition-shadow"
                    >
                      <p className="text-xs font-medium uppercase text-slate-400">Meetings Needing Prep</p>
                      <p className="mt-1 text-2xl font-bold text-slate-900">{loop.meetings.length}</p>
                    </div>
                    <div
                      onClick={() => setCurrentView('deals')}
                      className="cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:shadow-md transition-shadow"
                    >
                      <p className="text-xs font-medium uppercase text-slate-400">Deals Requiring Attention</p>
                      <p className="mt-1 text-2xl font-bold text-slate-900">
                        {loop.recommended_actions.filter(a => a.source === 'projects_deals').length}
                      </p>
                    </div>
                    <div
                      onClick={() => setCurrentView('forecast')}
                      className="cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:shadow-md transition-shadow"
                    >
                      <p className="text-xs font-medium uppercase text-slate-400">Staffing Exceptions</p>
                      <p className="mt-1 text-2xl font-bold text-slate-900">{loop.people_staffing.length}</p>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-white p-4">
                      <p className="text-xs font-medium uppercase text-slate-400">Workspace Context</p>
                      <p className="mt-1 text-2xl font-bold text-slate-900">{loop.meetings.length}</p>
                    </div>
                  </div>
                </section>

                <section aria-label="Recommended Actions" className="mt-6">
                  <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
                    Top Actions
                  </h2>
                  <ActionInbox
                    actionSummary={loop.action_summary}
                    actionGroups={loop.action_groups}
                    recommendedActions={loop.recommended_actions}
                    onRunRecorded={handleRunRecorded}
                  />
                </section>
              </>
            )}

            <details className="mt-8 group">
              <summary className="cursor-pointer text-sm font-semibold text-slate-400 hover:text-slate-600 select-none">
                <span className="group-open:hidden">▶ </span>
                <span className="hidden group-open:inline">▼ </span>
                Advanced
              </summary>
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-3 mt-4">
                <div className="xl:col-span-2">
                  <CommandCenter onRunRecorded={handleRunRecorded} onEstimate={setEstimate} />
                </div>
                <div className="space-y-4">
                  <TokenBudgetPanel estimate={estimate} />
                  <RecentRuns refreshKey={runsRefreshKey} />
                </div>
              </div>
            </details>
          </Layout>
        )}

        {currentView === 'deals' && <DealsView />}
        {currentView === 'forecast' && <ForecastView />}
        {currentView === 'meetings' && <MeetingsView />}

        {currentView === 'staffing' && (
          <Layout>
            <section aria-label="System Status">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">System Status</h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {status.map((card) => (
                  <StatusCard key={card.id} data={card} />
                ))}
              </div>
            </section>
            {loop && (
              <section aria-label="Daily Operating Loop" className="mt-8">
                <div className="mb-2 flex items-baseline justify-between">
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Daily Operating Loop</h2>
                  <span className="text-xs text-slate-400">{loop.date}</span>
                </div>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <DailySection title="People / Staffing" items={loop.people_staffing} />
                  <DailySection title="Meetings" items={loop.meetings} />
                  <DailySection title="Projects / Deals" items={loop.projects_deals} />
                  <DailySection title="Document Gaps" items={loop.document_gaps} />
                </div>
                {loop.warnings.length > 0 && (
                  <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                    {loop.warnings.join(' · ')}
                  </div>
                )}
              </section>
            )}
          </Layout>
        )}

        {currentView === 'projects' && (
          <Layout>
            <div className="space-y-6">
              <h1 className="text-2xl font-bold text-slate-900">Projects</h1>
              {loop && (
                <>
                  <DailySection title="Projects / Deals" items={loop.projects_deals} />
                  <DailySection title="Document Gaps" items={loop.document_gaps} />
                </>
              )}
            </div>
          </Layout>
        )}

        {currentView === 'archive' && (
          <Layout>
            <div className="space-y-6">
              <h1 className="text-2xl font-bold text-slate-900">Project Archive</h1>
              <p className="text-sm text-slate-400">Archive view — under Advanced.</p>
            </div>
          </Layout>
        )}
      </main>
    </div>
  )
}

export default App
