import type { StatusCardData } from '../api/client'

const FRESHNESS_STYLES: Record<StatusCardData['freshness'], string> = {
  fresh: 'text-emerald-600',
  stale: 'text-amber-600',
  missing: 'text-red-600',
  unknown: 'text-slate-400',
}

const FRESHNESS_LABELS: Record<StatusCardData['freshness'], string> = {
  fresh: 'Fresh',
  stale: 'Stale',
  missing: 'Missing',
  unknown: 'Unknown',
}

export function StatusCard({ data }: { data: StatusCardData }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">{data.label}</h3>
        <span className={`text-xs font-semibold uppercase ${FRESHNESS_STYLES[data.freshness]}`}>
          {FRESHNESS_LABELS[data.freshness]}
        </span>
      </div>
      <p className="mt-2 text-sm text-slate-500">{data.detail}</p>
      {typeof data.count === 'number' && (
        <p className="mt-1 text-2xl font-bold text-slate-900">{data.count}</p>
      )}
    </div>
  )
}
