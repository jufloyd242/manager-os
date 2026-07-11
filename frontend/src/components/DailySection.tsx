interface DailySectionProps {
  title: string
  items: unknown[]
  emptyLabel?: string
}

function renderItem(item: unknown): string {
  if (item && typeof item === 'object') {
    return Object.entries(item as Record<string, unknown>)
      .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${String(value)}`)
      .join(' · ')
  }
  return String(item)
}

export function DailySection({ title, items, emptyLabel = 'Nothing to show' }: DailySectionProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">{emptyLabel}</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {items.map((item, idx) => (
            <li key={idx} className="text-sm text-slate-600">
              {renderItem(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
