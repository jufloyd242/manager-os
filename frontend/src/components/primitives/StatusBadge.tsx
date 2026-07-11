interface StatusBadgeProps {
  status: string
  className?: string
}

const STATUS_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-800 border-red-200',
  high: 'bg-orange-100 text-orange-800 border-orange-200',
  medium: 'bg-yellow-100 text-yellow-800 border-yellow-200',
  low: 'bg-green-100 text-green-800 border-green-200',
  none: 'bg-slate-100 text-slate-500 border-slate-200',
  fresh: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  stale: 'bg-amber-50 text-amber-700 border-amber-200',
  missing: 'bg-red-50 text-red-700 border-red-200',
  unknown: 'bg-slate-100 text-slate-500 border-slate-200',
  overallocated: 'bg-red-100 text-red-800 border-red-200',
  underutilized: 'bg-amber-100 text-amber-800 border-amber-200',
  available: 'bg-blue-100 text-blue-800 border-blue-200',
  normal: 'bg-green-100 text-green-800 border-green-200',
}

export function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  const colorClass = STATUS_COLORS[status] || STATUS_COLORS.none
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${colorClass} ${className}`}>
      {status}
    </span>
  )
}
