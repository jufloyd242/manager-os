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

  return (
    <Layout>
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
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Daily Operating Loop
            </h2>
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

      {loop && (
        <section aria-label="Recommended Actions" className="mt-8">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Recommended Actions
          </h2>
          <ActionInbox
            actionSummary={loop.action_summary}
            actionGroups={loop.action_groups}
            recommendedActions={loop.recommended_actions}
            onRunRecorded={handleRunRecorded}
          />
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
    </Layout>
  )
}

export default App
