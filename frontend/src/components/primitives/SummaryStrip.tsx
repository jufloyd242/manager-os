interface SummaryItemProps {
  label: string
  value: string | number
  status?: string
  onClick?: () => void
  className?: string
}

function SummaryItem({ label, value, status, onClick, className = '' }: SummaryItemProps) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      onClick={onClick}
      className={`text-left px-4 py-2 border-r border-slate-200 last:border-r-0 ${className} ${
        onClick ? 'hover:bg-slate-50 cursor-pointer' : ''
      }`}
    >
      <p className="text-[10px] font-medium uppercase text-slate-400">{label}</p>
      <p className="text-lg font-bold text-slate-900 leading-tight">{value}</p>
      {status && <p className="text-[10px] text-slate-400">{status}</p>}
    </Tag>
  )
}

interface SummaryStripProps {
  items: SummaryItemProps[]
}

export function SummaryStrip({ items }: SummaryStripProps) {
  return (
    <div className="flex bg-white border border-slate-200 rounded-lg overflow-hidden shrink-0">
      {items.map((item, i) => (
        <SummaryItem key={i} {...item} className="flex-1" />
      ))}
    </div>
  )
}
