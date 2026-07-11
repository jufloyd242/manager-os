import { useState, useEffect, useCallback } from 'react'
import { getWorkspaceContext } from '../../api/client'
import type { WorkspaceContextResponse } from '../../api/client'

interface WorkspaceContextPanelProps {
  date?: string
  entityType?: string
  entity?: string
  lookbackDays?: number
  initialCollapsed?: boolean
  title?: string
  maxItems?: number
}

export function WorkspaceContextPanel({
  date,
  entityType,
  entity,
  lookbackDays = 0,
  initialCollapsed = true,
  title = 'Workspace Context',
  maxItems = 20,
}: WorkspaceContextPanelProps) {
  const [data, setData] = useState<WorkspaceContextResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(initialCollapsed)
  const [expandedItem, setExpandedItem] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getWorkspaceContext({
        date: date || new Date().toISOString().split('T')[0],
        lookback_days: lookbackDays,
        entity_type: entityType,
        entity: entity,
        limit: maxItems,
      })
      if (result.isMock) {
        setError('Backend unavailable')
        setData(null)
      } else {
        setData(result.data)
      }
    } catch {
      setError('Failed to load workspace context')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [date, entityType, entity, lookbackDays, maxItems])

  useEffect(() => { if (!collapsed) load() }, [collapsed, load])

  const confidenceColor = (c: string) => {
    if (c === 'high') return 'text-green-600'
    if (c === 'medium') return 'text-amber-600'
    return 'text-slate-400'
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
      >
        <span>{title}</span>
        <div className="flex items-center gap-2">
          {data && !collapsed && (
            <span className="text-xs text-slate-400">
              {data.linked_count} linked · {data.attention_count} attention
            </span>
          )}
          <svg
            className={`w-4 h-4 transition-transform ${collapsed ? '' : 'rotate-90'}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </button>

      {!collapsed && (
        <div className="border-t border-slate-100 px-4 py-3">
          {loading ? (
            <p className="text-sm text-slate-400">Loading...</p>
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : data && data.context_items.length > 0 ? (
            <div className="space-y-2">
              <div className="flex gap-3 text-xs text-slate-400">
                <span>{data.selected_date}</span>
                {data.latest_actual_source_date && (
                  <span>Latest: {data.latest_actual_source_date}</span>
                )}
                <span className="capitalize">{data.freshness}</span>
              </div>
              {data.context_items.slice(0, maxItems).map((item, i) => (
                <div
                  key={i}
                  className={`rounded-lg border p-3 text-sm ${
                    item.is_attention ? 'border-amber-200 bg-amber-50' : 'border-slate-100'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`text-xs font-medium uppercase ${confidenceColor(item.confidence)}`}>
                          {item.confidence}
                        </span>
                        {item.entity_name && (
                          <span className="text-xs text-indigo-600">{item.entity_name}</span>
                        )}
                      </div>
                      <p className="mt-1 text-slate-700">{item.excerpt}</p>
                    </div>
                    {item.is_attention && (
                      <span className="shrink-0 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
                        Attention
                      </span>
                    )}
                  </div>

                  <button
                    onClick={() => setExpandedItem(expandedItem === `${i}` ? null : `${i}`)}
                    className="mt-1 text-xs text-indigo-500 hover:text-indigo-700 cursor-pointer"
                  >
                    Why this context?
                  </button>

                  {expandedItem === `${i}` && (
                    <div className="mt-2 rounded bg-slate-50 p-2 text-xs text-slate-500 space-y-1">
                      <p><span className="font-medium">Source:</span> {item.source_path}</p>
                      <p><span className="font-medium">Source type:</span> {item.source_type}</p>
                      <p><span className="font-medium">Date:</span> {item.source_date || '—'}</p>
                      <p><span className="font-medium">Link method:</span> {item.link_method}</p>
                      <p><span className="font-medium">Evidence:</span> {item.link_evidence}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-400">No workspace context found for this date.</p>
          )}
        </div>
      )}
    </div>
  )
}