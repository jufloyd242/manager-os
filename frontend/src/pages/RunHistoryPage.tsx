import { PageHeader } from '../components/PageHeader'
import { RecentRuns } from '../components/RecentRuns'

interface RunHistoryPageProps {
  refreshKey: number
}

export function RunHistoryPage({ refreshKey }: RunHistoryPageProps) {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Run History" description="What operations ran and what happened?" />
      <div className="flex-1 overflow-y-auto p-6">
        <RecentRuns refreshKey={refreshKey} />
      </div>
    </div>
  )
}
