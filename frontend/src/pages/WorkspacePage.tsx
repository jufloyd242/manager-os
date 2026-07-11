import { useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceContextPanel } from '../features/workspaceContext/WorkspaceContextPanel'

export function WorkspacePage() {
  const [date] = useState(new Date().toISOString().split('T')[0])

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Workspace" description="What changed in my work context?" />
      <div className="flex-1 overflow-y-auto p-6">
        <WorkspaceContextPanel date={date} lookbackDays={7} initialCollapsed={false} maxItems={50} />
      </div>
    </div>
  )
}
