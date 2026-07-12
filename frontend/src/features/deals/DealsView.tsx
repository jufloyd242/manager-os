import { useState, useEffect, useCallback, useRef } from 'react'
import { getDeals, postRefresh } from '../../api/client'
import type { DealsResponse } from '../../api/client'

type SortField = 'attention' | 'close_date' | 'amount' | 'account'

const ATTENTION_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4,
}

function attentionBadge(level: string): string {
  const colors: Record<string, string> = {
    critical: 'bg-red-100 text-red-800 border-red-200',
    high: 'bg-orange-100 text-orange-800 border-orange-200',
    medium: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    low: 'bg-green-100 text-green-800 border-green-200',
    none: 'bg-slate-100 text-slate-500 border-slate-200',
  }
  return colors[level] || colors.none
}

export function DealsView() {
  const [data, setData] = useState<DealsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [stageFilter, setStageFilter] = useState('')
  const [ownerFilter, setOwnerFilter] = useState('')
  const [sortField, setSortField] = useState<SortField>('attention')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [selectedDealId, setSelectedDealId] = useState<string | null>(null)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Debounce search
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(search)
    }, 300)
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current)
    }
  }, [search])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getDeals({
        search: debouncedSearch || undefined,
        attention_only: attentionOnly || undefined,
        stage: stageFilter || undefined,
        owner: ownerFilter || undefined,
        limit: 200,
      })
      setData(result.data)
    } catch {
      setError('Failed to load deals')
    } finally {
      setLoading(false)
    }
  }, [debouncedSearch, attentionOnly, stageFilter, ownerFilter])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshError(null)
    try {
      await postRefresh({ sources: ['deals'], run_extraction: true })
      await load()
    } catch {
      setRefreshError('Refresh failed — displaying previous data')
      // Preserve existing data on failure
    } finally {
      setRefreshing(false)
    }
  }

  const deals = data?.deals || []
  const stages = [...new Set(deals.map(d => d.stage).filter(Boolean))] as string[]
  const owners = [...new Set(deals.map(d => d.technical_owner).filter(Boolean))] as string[]

  // Sort deals
  const sortedDeals = [...deals].sort((a, b) => {
    switch (sortField) {
      case 'attention':
        return (ATTENTION_ORDER[a.attention_level] ?? 5) - (ATTENTION_ORDER[b.attention_level] ?? 5)
      case 'close_date':
        return (a.close_date || '9999').localeCompare(b.close_date || '9999')
      case 'amount':
        return (b.services_amount || 0) - (a.services_amount || 0)
      case 'account':
        return (a.account || '').localeCompare(b.account || '')
      default:
        return 0
    }
  })

  const selectedDeal = selectedDealId ? deals.find(d => d.deal_id + d.deal_name === selectedDealId) : null

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-slate-200 bg-white">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search deals..."
          className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
        />
        <select value={attentionOnly ? 'true' : 'false'} onChange={(e) => setAttentionOnly(e.target.value === 'true')}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="false">All deals</option>
          <option value="true">Attention only</option>
        </select>
        <select value={stageFilter} onChange={(e) => setStageFilter(e.target.value)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">All stages</option>
          {stages.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={ownerFilter} onChange={(e) => setOwnerFilter(e.target.value)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">All owners</option>
          {owners.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
        <select value={sortField} onChange={(e) => setSortField(e.target.value as SortField)}
          className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
          <option value="attention">Sort: Attention</option>
          <option value="close_date">Sort: Close date</option>
          <option value="amount">Sort: Amount</option>
          <option value="account">Sort: Account</option>
        </select>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer shrink-0"
        >
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* Freshness + errors */}
      <div className="shrink-0 px-4 py-1.5 text-xs text-slate-500 bg-slate-50 border-b border-slate-200">
        {data && (
          <span>{data.total} deals · {data.attention_count} need attention · freshness: {data.freshness}</span>
        )}
        {refreshError && <span className="text-amber-600 ml-2">⚠ {refreshError}</span>}
      </div>

      {/* Master-detail */}
      <div className="flex-1 flex overflow-hidden">
        {/* Deal list */}
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {loading ? (
            <div className="p-8 text-center text-sm text-slate-400">Loading...</div>
          ) : error ? (
            <div className="p-8 text-center text-sm text-red-600">{error}</div>
          ) : sortedDeals.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-400">No deals found.</div>
          ) : (
            <div className="divide-y divide-slate-100">
              {sortedDeals.map((deal) => {
                const dealKey = deal.deal_id + deal.deal_name
                const isSelected = selectedDealId === dealKey
                return (
                  <button
                    key={dealKey}
                    onClick={() => setSelectedDealId(dealKey)}
                    className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors cursor-pointer ${
                      isSelected ? 'bg-indigo-50 border-l-4 border-l-indigo-500' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`rounded-full border px-2 py-0.5 text-xs font-medium shrink-0 ${attentionBadge(deal.attention_level)}`}>
                            {deal.attention_level}
                          </span>
                          <p className="text-sm font-medium text-slate-900 truncate">{deal.deal_name}</p>
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                          <span className="truncate">{deal.account}</span>
                          {deal.stage && <span className="shrink-0">{deal.stage}</span>}
                        </div>
                      </div>
                      <div className="text-right text-xs shrink-0">
                        {deal.close_date && (
                          <p className={deal.days_until_close !== null && deal.days_until_close <= 7 ? 'font-semibold text-red-600' : 'text-slate-600'}>
                            {deal.days_until_close !== null
                              ? deal.days_until_close < 0
                                ? `${Math.abs(deal.days_until_close)}d overdue`
                                : `${deal.days_until_close}d`
                              : 'No date'}
                          </p>
                        )}
                        {deal.services_amount != null && (
                          <p className="text-slate-500">${deal.services_amount.toLocaleString()}</p>
                        )}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Deal detail */}
        <div className="w-1/2 overflow-y-auto p-6">
          {!selectedDeal ? (
            <div className="text-center text-sm text-slate-400 py-8">Select a deal to see details.</div>
          ) : (
            <div className="space-y-4">
              <div>
                <div className="flex items-center gap-2">
                  <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${attentionBadge(selectedDeal.attention_level)}`}>
                    {selectedDeal.attention_level}
                  </span>
                  <h2 className="text-lg font-bold text-slate-900">{selectedDeal.deal_name}</h2>
                </div>
                <p className="text-sm text-slate-500 mt-1">{selectedDeal.account}</p>
              </div>

              {selectedDeal.attention_reasons.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-1">Attention Reasons</h3>
                  <div className="flex flex-wrap gap-2">
                    {selectedDeal.attention_reasons.map((r, i) => (
                      <span key={i} className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">{r}</span>
                    ))}
                  </div>
                </div>
              )}

              <dl className="text-sm space-y-1.5">
                {selectedDeal.close_date && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Close Date</dt>
                    <dd className={selectedDeal.days_until_close !== null && selectedDeal.days_until_close <= 7 ? 'text-red-600 font-medium' : 'text-slate-700'}>
                      {selectedDeal.close_date}
                      {selectedDeal.days_until_close !== null && ` (${selectedDeal.days_until_close}d)`}
                    </dd>
                  </div>
                )}
                {selectedDeal.services_amount != null && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Amount</dt>
                    <dd className="text-slate-700">${selectedDeal.services_amount.toLocaleString()}</dd>
                  </div>
                )}
                {selectedDeal.forecast_category && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Forecast Category</dt>
                    <dd className="text-slate-700">{selectedDeal.forecast_category}</dd>
                  </div>
                )}
                {selectedDeal.probability != null && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Probability</dt>
                    <dd className="text-slate-700">{(selectedDeal.probability * 100).toFixed(0)}%</dd>
                  </div>
                )}
                {selectedDeal.sow_status && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">SOW</dt>
                    <dd className="text-slate-700">{selectedDeal.sow_status}</dd>
                  </div>
                )}
                {selectedDeal.loe_status && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">LOE</dt>
                    <dd className="text-slate-700">{selectedDeal.loe_status}</dd>
                  </div>
                )}
                {selectedDeal.staffing_feasibility && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Staffing Feasibility</dt>
                    <dd className="text-slate-700">{selectedDeal.staffing_feasibility}</dd>
                  </div>
                )}
                {selectedDeal.technical_owner && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Technical Owner</dt>
                    <dd className="text-slate-700">{selectedDeal.technical_owner}</dd>
                  </div>
                )}
                {selectedDeal.ae_name && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">AE</dt>
                    <dd className="text-slate-700">{selectedDeal.ae_name}</dd>
                  </div>
                )}
              </dl>

              {selectedDeal.blockers && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-1">Blockers</h3>
                  <p className="text-sm text-slate-600">{selectedDeal.blockers}</p>
                </div>
              )}
              {selectedDeal.next_action && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-1">Next Action</h3>
                  <p className="text-sm text-slate-600">{selectedDeal.next_action}</p>
                </div>
              )}

              <div className="border-t border-slate-100 pt-3">
                <p className="text-xs text-slate-400">Freshness: {selectedDeal.freshness}</p>
                {selectedDeal.freshness_explanation && (
                  <p className="text-xs text-slate-400 mt-0.5">{selectedDeal.freshness_explanation}</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
