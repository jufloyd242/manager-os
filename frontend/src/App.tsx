import { useState, useEffect } from 'react'
import { AppShell } from './components/AppShell'
import { useHashRoute } from './hooks/useHashRoute'
import { TodayPage } from './pages/TodayPage'
import { ActionsPage } from './pages/ActionsPage'
import { MeetingsPage } from './pages/MeetingsPage'
import { DealsPage } from './pages/DealsPage'
import { ForecastPage } from './pages/ForecastPage'
import { WorkspacePage } from './pages/WorkspacePage'
import { PeoplePage } from './pages/PeoplePage'
import { ProjectsPage } from './pages/ProjectsPage'
import { DataHealthPage } from './pages/DataHealthPage'
import { RefreshHistoryPage } from './pages/RefreshHistoryPage'
import { CommandsPage } from './pages/CommandsPage'
import { RunHistoryPage } from './pages/RunHistoryPage'
import { TokenBudgetPage } from './pages/TokenBudgetPage'
import { ProjectArchivePage } from './pages/ProjectArchivePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { getStatus } from './api/client'
import type { RunRecord, TokenEstimate } from './api/client'

function App() {
  const [route, navigate] = useHashRoute()
  const [estimate, setEstimate] = useState<TokenEstimate | null>(null)
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
  const [badges, setBadges] = useState<Record<string, number>>({})

  useEffect(() => {
    getStatus()
      .then((result) => {
        const b: Record<string, number> = {}
        const obsidian = result.data.find(s => s.id === 'obsidian')
        if (obsidian && obsidian.freshness !== 'fresh') {
          b['data-health'] = (b['data-health'] || 0) + 1
        }
        setBadges(b)
      })
      .catch(() => {
        // Badges are optional
      })
  }, [route])

  function handleRunRecorded(_run: RunRecord) {
    setRunsRefreshKey((key) => key + 1)
  }

  function renderPage() {
    switch (route) {
      case 'today':
        return <TodayPage onNavigate={navigate} onRunRecorded={handleRunRecorded} />
      case 'actions':
        return <ActionsPage onRunRecorded={handleRunRecorded} />
      case 'meetings':
        return <MeetingsPage />
      case 'deals':
        return <DealsPage />
      case 'forecast':
        return <ForecastPage />
      case 'workspace':
        return <WorkspacePage />
      case 'people':
        return <PeoplePage />
      case 'projects':
        return <ProjectsPage />
      case 'data-health':
        return <DataHealthPage />
      case 'refresh-history':
        return <RefreshHistoryPage />
      case 'commands':
        return <CommandsPage onRunRecorded={handleRunRecorded} onEstimate={setEstimate} />
      case 'run-history':
        return <RunHistoryPage refreshKey={runsRefreshKey} />
      case 'token-budget':
        return <TokenBudgetPage estimate={estimate} />
      case 'project-archive':
        return <ProjectArchivePage />
      default:
        return <NotFoundPage />
    }
  }

  return (
    <AppShell currentRoute={route} onNavigate={navigate} badges={badges}>
      {renderPage()}
    </AppShell>
  )
}

export default App
