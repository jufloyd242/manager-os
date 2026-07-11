import { useState, useEffect, useCallback } from 'react'
import { getDeals, postRefresh } from '../../api/client'
import type { DealsResponse } from '../../api/client'

export function DealsView() {
  const [data, setData] = useState<DealsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [expandedDeal, setExpandedDeal] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getDeals({ search: search || undefined, attention_only: attentionOnly || undefined, limit: 200 })
      setData(result.data)
    } catch {
      setError('Failed to load deals')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [search, attentionOnly])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    await postRefresh({ sources: ['deals'], run_extraction: true })
    await load()
    setRefreshing(false)
  }

  const attentionBadge = (level: string) => {
    const colors: Record<string, string> = {
      critical: 'bg-red-100 text-red-800 border-red-200',
      high: 'bg-orange-100 text-orange-800 border-orange-200',
      medium: 'bg-yellow-100 text-yellow-800 border-yellow-200',
      low: 'bg-green-100 text-green-800 border-green-200',
      none: 'bg-slate-100 text-slate-500 border-slate-200',
    }
    return colors[level] || colors.none
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Deals</h1>
          {data && (
            <p className="text-sm text-slate-500">
              {data.total} deals · {data.attention_count} need attention · freshness: {data.freshness}
            </p>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer"
        >
          {refreshing ? 'Refreshing...' : 'Refresh from file'}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex gap-4">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search deals..."
          className="flex-1 rounded-lg border border-slate-300 px-4 py-2 text-sm focus:border-indigo-500 focus:outline-none"
        />
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={attentionOnly}
            onChange={(e) => setAttentionOnly(e.target.checked)}
            className="rounded border-slate-300"
          />
          Attention only
        </label>
      </div>

      {loading ? (
        <div className="text-sm text-slate-400">Loading...</div>
      ) : data && data.deals.length > 0 ? (
        <div className="space-y-3">
          {data.deals.map((deal) => (
            <div
              key={deal.deal_id + deal.deal_name}
              className={`rounded-lg border p-4 transition-shadow hover:shadow-md ${
                deal.attention_level === 'critical' ? 'border-red-300 bg-red-50' :
                deal.attention_level === 'high' ? 'border-orange-300 bg-orange-50' :
                'border-slate-200 bg-white'
              }`}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <h3 className="font-semibold text-slate-900">{deal.deal_name}</h3>
                    <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${attentionBadge(deal.attention_level)}`}>
                      {deal.attention_level}
                    </span>
                    {deal.stage && (
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{deal.stage}</span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{deal.account}</p>
                </div>
                <div className="text-right text-sm">
                  {deal.close_date && (
                    <p className={deal.days_until_close !== null && deal.days_until_close <= 7 ? 'font-semibold text-red-600' : 'text-slate-600'}>
                      {deal.days_until_close !== null
                        ? deal.days_until_close < 0
                          ? `${Math.abs(deal.days_until_close)}d overdue`
                          : `${deal.days_until_close}d remaining`
                        : 'No close date'}
                    </p>
                  )}
                  {deal.services_amount && (
                    <p className="text-slate-500">${deal.services_amount.toLocaleString()}</p>
                  )}
                </div>
              </div>

              {deal.attention_reasons.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {deal.attention_reasons.map((r, i) => (
                    <span key={i} className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">{r}</span>
                  ))}
                </div>
              )}

              <button
                onClick={() => setExpandedDeal(expandedDeal === deal.deal_id ? null : deal.deal_id)}
                className="mt-2 text-xs font-medium text-indigo-600 hover:text-indigo-800 cursor-pointer"
              >
                {expandedDeal === deal.deal_id ? 'Less details' : 'More details'}
              </button>

              {expandedDeal === deal.deal_id && (
                <div className="mt-3 grid grid-cols-2 gap-4 border-t border-slate-100 pt-3 text-sm">
                  <div>
                    <p className="text-xs text-slate-400">Technical Owner</p>
                    <p className="text-slate-700">{deal.technical_owner || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">AE</p>
                    <p className="text-slate-700">{deal.ae_name || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">SOW Status</p>
                    <p className="text-slate-700">{deal.sow_status || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">LOE Status</p>
                    <p className="text-slate-700">{deal.loe_status || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Staffing Feasibility</p>
                    <p className="text-slate-700">{deal.staffing_feasibility || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Forecast Category</p>
                    <p className="text-slate-700">{deal.forecast_category || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Blocker</p>
                    <p className="text-slate-700">{deal.blockers || '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Next Action</p>
                    <p className="text-slate-700">{deal.next_action || '—'}</p>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-8 text-center text-sm text-slate-400">
          No deals found. Refresh from configured file.
        </div>
      )}
    </div>
  )
}