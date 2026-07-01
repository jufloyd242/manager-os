import type { RecommendedAction } from '../api/client'

const PRIORITY_STYLES: Record<RecommendedAction['priority'], string> = {
  high: 'border-l-4 border-red-500',
  medium: 'border-l-4 border-amber-500',
  low: 'border-l-4 border-slate-300',
}

export function RecommendedActionCard({ action }: { action: RecommendedAction }) {
  return (
    <div className={`rounded-lg bg-white p-3 shadow-sm ${PRIORITY_STYLES[action.priority]}`}>
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-800">{action.title}</h4>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-500">
          {action.priority}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{action.reason}</p>
      <code className="mt-2 block truncate rounded bg-slate-50 px-2 py-1 text-xs text-slate-600">
        {action.command}
      </code>
    </div>
  )
}
