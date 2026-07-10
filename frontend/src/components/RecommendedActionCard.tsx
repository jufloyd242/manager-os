import { useState } from 'react'
import type { RecommendedAction, RunRecord } from '../api/client'
import { postFeedback } from '../api/client'
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
  const [feedbackStatus, setFeedbackStatus] = useState<string | null>(null)

  const handleFeedback = async (rating: string) => {
    try {
      const itemId = action.id || `action:${action.title.replace(/\s+/g, '_')}`
      await postFeedback(itemId, rating)
      setFeedbackStatus(rating)
    } catch (err) {
      console.error(err)
    }
  }

  if (feedbackStatus === 'wrong' || feedbackStatus === 'stale' || feedbackStatus === 'noisy') {
    return (
      <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-400 border border-slate-200">
        Action hidden based on feedback ({feedbackStatus}).
      </div>
    )
  }

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

      {action.why_it_matters && (
        <div className="text-xs bg-slate-50/50 p-2.5 rounded-md border border-slate-100">
          <span className="font-bold text-slate-700 block mb-0.5">Why it matters:</span>
          <p className="text-slate-600 leading-relaxed">{action.why_it_matters}</p>
        </div>
      )}

      {action.recommended_next_action && (
        <div className="text-xs">
          <span className="font-bold text-slate-700">Next Action:</span> {action.recommended_next_action}
        </div>
      )}

      {/* Action metadata footer */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-400 border-t border-slate-50 pt-2.5">
        {action.entity && (
          <div>
            <span className="font-semibold text-slate-500">Entity:</span> {action.entity}
          </div>
        )}
        {action.source && (
          <div>
            <span className="font-semibold text-slate-500">Source:</span> {action.source}
          </div>
        )}
        {action.source_date && (
          <div>
            <span className="font-semibold text-slate-500">Source Date:</span> {action.source_date}
          </div>
        )}
        {action.last_refreshed && (
          <div>
            <span className="font-semibold text-slate-500">Refreshed:</span> {action.last_refreshed}
          </div>
        )}
        {typeof action.confidence === 'number' && (
          <div>
            <span className="font-semibold text-slate-500">Confidence:</span> {(action.confidence * 100).toFixed(0)}%
          </div>
        )}
        {action.explanation && (
          <div className="w-full mt-1 text-slate-400 italic">
            <span className="font-semibold text-slate-500">Match Reason:</span> {action.explanation}
          </div>
        )}
      </div>

      {action.command && (
        <code className="block truncate rounded bg-slate-50 px-2 py-1 text-xs text-slate-600 font-mono">
          {action.command}
        </code>
      )}

      {action.primary_command && <DailyActionButtons action={action} onRunRecorded={onRunRecorded} />}

      {/* Feedback Action Controls */}
      <div className="flex flex-wrap gap-1 border-t border-slate-100 pt-2.5">
        <button
          onClick={() => handleFeedback('useful')}
          className={`px-2 py-1 text-[10px] font-bold rounded border transition-colors ${
            feedbackStatus === 'useful'
              ? 'bg-emerald-50 text-emerald-700 border-emerald-300'
              : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
          }`}
        >
          ✓ Done / Useful
        </button>
        <button
          onClick={() => handleFeedback('wrong')}
          className="px-2 py-1 text-[10px] font-bold rounded border bg-white text-slate-600 border-slate-200 hover:bg-red-50 hover:text-red-700 hover:border-red-300 transition-colors"
        >
          Wrong
        </button>
        <button
          onClick={() => handleFeedback('stale')}
          className="px-2 py-1 text-[10px] font-bold rounded border bg-white text-slate-600 border-slate-200 hover:bg-amber-50 hover:text-amber-700 hover:border-amber-300 transition-colors"
        >
          Stale
        </button>
        <button
          onClick={() => handleFeedback('noisy')}
          className="px-2 py-1 text-[10px] font-bold rounded border bg-white text-slate-600 border-slate-200 hover:bg-rose-50 hover:text-rose-700 hover:border-rose-300 transition-colors"
        >
          Not Relevant
        </button>
        <button
          onClick={() => handleFeedback('noisy')}
          className="px-2 py-1 text-[10px] font-bold rounded border bg-white text-slate-600 border-slate-200 hover:bg-rose-50 hover:text-rose-700 hover:border-rose-300 transition-colors"
        >
          Not Mine
        </button>
        <button
          onClick={() => handleFeedback('missing-context')}
          className={`px-2 py-1 text-[10px] font-bold rounded border transition-colors ${
            feedbackStatus === 'missing-context'
              ? 'bg-blue-50 text-blue-700 border-blue-300'
              : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
          }`}
        >
          Needs Context
        </button>
      </div>
    </div>
  )
}
