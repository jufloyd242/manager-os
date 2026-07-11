import type { RecommendedAction, RunRecord } from '../api/client'
import { DailyActionButtons } from './DailyActionButtons'

const PRIORITY_STYLES: Record<RecommendedAction['priority'], string> = {
  high: 'border-l-4 border-red-500',
  medium: 'border-l-4 border-amber-500',
  low: 'border-l-4 border-slate-300',
}

export interface RecommendedActionCardProps {
  action: RecommendedAction
  onRunRecorded?: (run: RunRecord) => void
}

export function RecommendedActionCard({ action, onRunRecorded }: RecommendedActionCardProps) {
  return (
    <div className={`rounded-lg bg-white p-4 shadow-sm space-y-3 border border-slate-100 ${PRIORITY_STYLES[action.priority]}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-bold text-slate-900">{action.title}</h4>
          <p className="mt-1 text-xs text-slate-500 font-medium">Reason: {action.reason}</p>
        </div>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-500">
          {action.priority}
        </span>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-400 border-t border-slate-50 pt-2.5">
        {action.source && (
          <div>
            <span className="font-semibold text-slate-500">Source:</span> {action.source}
          </div>
        )}
        {action.id && (
          <div>
            <span className="font-semibold text-slate-500">ID:</span> {action.id}
          </div>
        )}
      </div>

      {action.command && (
        <code className="block truncate rounded bg-slate-50 px-2 py-1 text-xs text-slate-600 font-mono">
          {action.command}
        </code>
      )}

      {action.primary_command && <DailyActionButtons action={action} onRunRecorded={onRunRecorded} />}
    </div>
  )
}