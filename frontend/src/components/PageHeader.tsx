import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  description?: string
  freshness?: string
  primaryAction?: ReactNode
  secondaryActions?: ReactNode
}

export function PageHeader({ title, description, freshness, primaryAction, secondaryActions }: PageHeaderProps) {
  return (
    <div className="flex items-center justify-between gap-4 px-6 py-3 border-b border-slate-200 bg-white shrink-0">
      <div className="min-w-0">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-bold text-slate-900 truncate">{title}</h1>
          {freshness && (
            <span className="text-xs text-slate-400 whitespace-nowrap">{freshness}</span>
          )}
        </div>
        {description && (
          <p className="text-xs text-slate-500 mt-0.5 truncate">{description}</p>
        )}
      </div>
      {(primaryAction || secondaryActions) && (
        <div className="flex items-center gap-2 shrink-0">
          {secondaryActions}
          {primaryAction}
        </div>
      )}
    </div>
  )
}
