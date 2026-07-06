import { useEffect, useState } from 'react'
import { Layout } from './components/Layout'
import { StatusCard } from './components/StatusCard'
import { DailySection } from './components/DailySection'
import { ActionInbox } from './components/ActionInbox'
import { CommandCenter } from './components/CommandCenter'
import { RecentRuns } from './components/RecentRuns'
import { TokenBudgetPanel } from './components/TokenBudgetPanel'
import { getStatus, getDaily } from './api/client'
import { mockSystemStatus, mockDailyOperatingLoop } from './api/mockData'
import type { StatusCardData, DailyOperatingLoop, RunRecord, TokenEstimate } from './api/client'

function App() {
  const [activeView, setActiveView] = useState<'daily_loop' | 'staffing' | 'archive'>('daily_loop')
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [statusMock, setStatusMock] = useState(false)
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [loopMock, setLoopMock] = useState(false)
  const [estimate, setEstimate] = useState<TokenEstimate | null>(null)
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    getStatus()
      .then((result) => {
        setStatus(result.data)
        setStatusMock(result.isMock)
      })
      .catch(() => {
        setStatus(mockSystemStatus)
        setStatusMock(true)
      })
    getDaily()
      .then((result) => {
        setLoop(result.data)
        setLoopMock(result.isMock)
      })
      .catch(() => {
        setLoop(mockDailyOperatingLoop)
        setLoopMock(true)
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

  const usingMockData = statusMock || loopMock

  const legacyProjects = [
    { id: "project::OPP_LEGACY_1", name: "Legacy Empty Project", client: "Legacy Client", opportunity_number: "OPP_LEGACY_1", document_status: "LEGACY_EMPTY" },
    { id: "project::OPP-001", name: "Acme Phase 1", client: "Acme Corp", opportunity_number: "OPP-001", document_status: "ARCHIVED" },
    { id: "project::OPP-002", name: "Initech QA", client: "Initech", opportunity_number: "OPP-002", document_status: "LEGACY_EMPTY" }
  ]

  const badges = {
    daily_loop: loop?.recommended_actions?.length ?? 0,
    staffing: loop?.people_staffing?.length ?? 0,
    archive: legacyProjects.filter(p => p.document_status === 'LEGACY_EMPTY').length
  }

  return (
    <Layout
      currentView={activeView}
      onViewChange={setActiveView}
      badges={badges}
    >
      {toast && (
        <div className="mb-4 rounded-lg border border-slate-200 bg-slate-900 px-4 py-2 text-sm text-white shadow">
          {toast}
        </div>
      )}

      {usingMockData && (
        <div
          data-testid="dashboard-mock-indicator"
          className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-800"
        >
          Offline / Mock Data — the Manager OS API is unreachable, showing local mock data instead.
        </div>
      )}

      {/* DAILY_LOOP VIEW */}
      {activeView === 'daily_loop' && (
        <div className="space-y-8">
          <section aria-label="System Status">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">System Status</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {status.map((card) => (
                <StatusCard key={card.id} data={card} />
              ))}
            </div>
          </section>

          {loop && (
            <section aria-label="Recommended Actions" className="mt-8">
              <h2 className="mb-2 text-xl font-bold tracking-tight text-slate-900">Action Inbox</h2>
              <ActionInbox
                actionSummary={loop.action_summary}
                actionGroups={loop.action_groups}
                recommendedActions={loop.recommended_actions}
                onRunRecorded={handleRunRecorded}
              />
            </section>
          )}

          {loop && (
            <section aria-label="Daily Operating Loop" className="mt-8">
              <div className="mb-2 flex items-baseline justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
                  Daily Operating Loop Details
                </h2>
                <span className="text-xs text-slate-400">{loop.date}</span>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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

          <section aria-label="Command Center and Runs" className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <CommandCenter onRunRecorded={handleRunRecorded} onEstimate={setEstimate} />
            </div>
            <div className="space-y-4">
              <TokenBudgetPanel estimate={estimate} />
              <RecentRuns refreshKey={runsRefreshKey} />
            </div>
          </section>
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

          {/* Capacity Balancing Metrics Card */}
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm space-y-6">
            <div>
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400">Capacity Balancing Metrics</h3>
              <p className="text-xs text-slate-500 mt-1">Real-time balancing of team-member utilization targets</p>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-4 rounded-xl border border-slate-100 bg-slate-50/50">
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">Team Utilization Gaps</h4>
                <div className="space-y-3">
                  <div>
                    <div className="flex justify-between text-xs font-medium mb-1">
                      <span>Priya Nair (Overallocated)</span>
                      <span className="text-rose-600 font-bold">128% FTE</span>
                    </div>
                    <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                      <div className="bg-rose-500 h-full rounded-full" style={{ width: '100%' }}></div>
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between text-xs font-medium mb-1">
                      <span>Jordan Lee (Underutilized)</span>
                      <span className="text-amber-600 font-bold">80% FTE</span>
                    </div>
                    <div className="w-full bg-slate-200 h-2 rounded-full overflow-hidden">
                      <div className="bg-amber-500 h-full rounded-full" style={{ width: '80%' }}></div>
                    </div>
                  </div>
                </div>
              </div>
              
              <div className="p-4 rounded-xl border border-slate-100 bg-indigo-50/20">
                <h4 className="text-xs font-bold uppercase tracking-wider text-indigo-500 mb-2">Recommended Rebalancing</h4>
                <p className="text-xs text-slate-600 leading-relaxed">
                  Priya Nair is overallocated by <span className="font-bold text-rose-600">28%</span> on <span className="font-semibold text-slate-800">Acme Corp</span>. 
                  We recommend redistributing <span className="font-bold text-indigo-600">0.28 FTE</span> of her tasks to Jordan Lee to balance capacity.
                </p>
              </div>
            </div>
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
              <p className="text-xs text-slate-500 mt-1">Historical engagements and archived projects marked as LEGACY_EMPTY.</p>
            </div>
            
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
                  {legacyProjects.map((p) => (
                    <tr key={p.id} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-4 py-3.5 font-medium text-slate-900">{p.client}</td>
                      <td className="px-4 py-3.5">{p.name}</td>
                      <td className="px-4 py-3.5 font-mono">{p.opportunity_number}</td>
                      <td className="px-4 py-3.5">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                          p.document_status === 'LEGACY_EMPTY' ? 'bg-amber-50 text-amber-700 border border-amber-200' : 'bg-slate-100 text-slate-600'
                        }`}>
                          {p.document_status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </Layout>
  )
}

export default App
