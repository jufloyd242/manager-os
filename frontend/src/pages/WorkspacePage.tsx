import { useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceContextPanel } from '../features/workspaceContext/WorkspaceContextPanel'

export function WorkspacePage() {
  const [date] = useState(new Date().toISOString().split('T')[0])
  const [lookbackDays, setLookbackDays] = useState(7)
  const [entityType, setEntityType] = useState('')
  const [entity, setEntity] = useState('')
  const [attentionOnly, setAttentionOnly] = useState(false)

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Workspace" description="What changed in my work context?" />
      {/* Toolbar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-slate-200 bg-white flex-wrap">
        <label className="flex items-center gap-1 text-sm text-slate-600">
          Lookback:
          <select
            value={lookbackDays}
            onChange={(e) => setLookbackDays(Number(e.target.value))}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value={1}>1 day</option>
            <option value={3}>3 days</option>
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
          </select>
        </label>
        <select
          value={entityType}
          onChange={(e) => setEntityType(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">All entity types</option>
          <option value="person">Person</option>
          <option value="client">Client</option>
          <option value="deal">Deal</option>
          <option value="project">Project</option>
        </select>
        <input
          type="text"
          value={entity}
          onChange={(e) => setEntity(e.target.value)}
          placeholder="Entity name..."
          className="flex-1 min-w-[100px] rounded border border-slate-300 px-3 py-1 text-sm"
        />
        <button
          onClick={() => setAttentionOnly(!attentionOnly)}
          className={`rounded-full border px-3 py-1 text-xs font-medium cursor-pointer ${
            attentionOnly
              ? 'bg-amber-100 text-amber-800 border-amber-300'
              : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
          }`}
        >
          Attention only
        </button>
      </div>

      <div className="flex-1 overflow-hidden">
        <WorkspaceContextPanel
          date={date}
          lookbackDays={lookbackDays}
          entityType={entityType || undefined}
          entity={entity || undefined}
          initialCollapsed={false}
          maxItems={50}
        />
      </div>
    </div>
  )
}
