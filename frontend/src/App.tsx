import { useEffect, useState } from 'react'
import { Layout } from './components/Layout'
import { StatusCard } from './components/StatusCard'
import { DailySection } from './components/DailySection'
import { RecommendedActionCard } from './components/RecommendedActionCard'
import { CommandCenter } from './components/CommandCenter'
import { RecentRuns } from './components/RecentRuns'
import { TokenBudgetPanel } from './components/TokenBudgetPanel'
import { mockApiClient } from './api/mockData'
import type {
  StatusCardData,
  DailyOperatingLoop,
  CommandDefinition,
  RunRecord,
  TokenBudget,
} from './api/client'

function App() {
  const [status, setStatus] = useState<StatusCardData[]>([])
  const [loop, setLoop] = useState<DailyOperatingLoop | null>(null)
  const [commands, setCommands] = useState<CommandDefinition[]>([])
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [budget, setBudget] = useState<TokenBudget | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    mockApiClient.getSystemStatus().then(setStatus)
    mockApiClient.getDailyOperatingLoop().then(setLoop)
    mockApiClient.getCommandRegistry().then(setCommands)
    mockApiClient.getRecentRuns().then(setRuns)
    mockApiClient.getTokenBudget().then(setBudget)
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  async function handleRun(command: CommandDefinition, dryRun: boolean) {
    const record = await mockApiClient.runCommand(command.command_id, { dryRun })
    setRuns((prev) => [record, ...prev])
    setToast(`${dryRun ? 'Dry run' : 'Run'} queued for "${command.label}" (mock only — no network call).`)
  }

  return (
    <Layout>
      {toast && (
        <div className="mb-4 rounded-lg border border-slate-200 bg-slate-900 px-4 py-2 text-sm text-white shadow">
          {toast}
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
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {loop.recommended_actions.map((action) => (
              <RecommendedActionCard key={action.title} action={action} />
            ))}
          </div>
        </section>
      )}

      <section aria-label="Command Center and Runs" className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <CommandCenter commands={commands} onRun={handleRun} />
        </div>
        <div className="space-y-4">
          {budget && <TokenBudgetPanel budget={budget} />}
          <RecentRuns runs={runs} />
        </div>
      </section>
    </Layout>
  )
}

export default App
