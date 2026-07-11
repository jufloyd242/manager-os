import { PageHeader } from '../components/PageHeader'
import { CommandCenter } from '../components/CommandCenter'
import type { RunRecord, TokenEstimate } from '../api/client'

interface CommandsPageProps {
  onRunRecorded: (run: RunRecord) => void
  onEstimate: (estimate: TokenEstimate | null) => void
}

export function CommandsPage({ onRunRecorded, onEstimate }: CommandsPageProps) {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Commands" description="What guarded operations can I run?" />
      <div className="flex-1 overflow-y-auto p-6">
        <CommandCenter onRunRecorded={onRunRecorded} onEstimate={onEstimate} />
      </div>
    </div>
  )
}
