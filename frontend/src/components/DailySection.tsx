import { useState } from 'react'
import { getStaffingBalance } from '../api/client'
import type { StaffingBalanceResponse } from '../api/client'

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
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [balanceData, setBalanceData] = useState<StaffingBalanceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const isStaffing = title === 'People / Staffing'

  const handleOpenPreview = async () => {
    setIsModalOpen(true)
    setIsLoading(true)
    setError(null)
    try {
      const result = await getStaffingBalance()
      setBalanceData(result.data)
    } catch (err) {
      console.error(err)
      setError('Failed to load staffing rebalance data.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm relative">
      <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">{emptyLabel}</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {items.map((item: any, idx) => {
            if (isStaffing && item && typeof item === 'object') {
              const isOverallocated = item.signal?.toLowerCase().includes('overallocated')
              return (
                <li key={idx} className="flex flex-col gap-2 p-2.5 rounded-lg bg-slate-50/50 hover:bg-slate-50 transition-colors border border-slate-100">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <span className="font-semibold text-slate-900 text-sm">{item.person}</span>
                      <p className="text-xs text-slate-600 mt-0.5">{item.signal}</p>
                    </div>
                    {isOverallocated && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-rose-50 text-rose-700 border border-rose-200 shadow-sm shrink-0">
                        Overallocated
                      </span>
                    )}
                  </div>
                  {isOverallocated && (
                    <button
                      onClick={handleOpenPreview}
                      className="cursor-pointer mt-1 self-start px-3 py-1.5 rounded-md text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white shadow-sm transition-all duration-150 flex items-center gap-1 hover:shadow-md"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2" />
                      </svg>
                      Preview Rebalance
                    </button>
                  )}
                </li>
              )
            }

            return (
              <li key={idx} className="text-sm text-slate-600">
                {renderItem(item)}
              </li>
            )
          })}
        </ul>
      )}

      {/* High-Fidelity Premium Modal Overlay */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/60 backdrop-blur-sm transition-opacity duration-300">
          <div className="w-full max-w-2xl bg-white rounded-2xl border border-slate-100 shadow-2xl overflow-hidden flex flex-col max-h-[85vh] transform scale-100 transition-all duration-300">
            
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-lg bg-indigo-50 text-indigo-600">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-bold text-slate-900">Staffing Rebalance Preview</h3>
                  <p className="text-xs text-slate-500 mt-0.5">Simulating optimal capacity distributions across active team members</p>
                </div>
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Modal Scrollable Content */}
            <div className="p-6 overflow-y-auto space-y-6 flex-1">
              {isLoading ? (
                <div className="py-12 flex flex-col items-center justify-center space-y-4">
                  <div className="relative w-12 h-12">
                    <div className="absolute inset-0 rounded-full border-4 border-slate-100"></div>
                    <div className="absolute inset-0 rounded-full border-4 border-indigo-600 border-t-transparent animate-spin"></div>
                  </div>
                  <p className="text-sm text-slate-500 font-medium">Analyzing capacity allocations...</p>
                </div>
              ) : error ? (
                <div className="p-4 bg-rose-50 border border-rose-100 rounded-xl text-center text-sm text-rose-700">
                  {error}
                </div>
              ) : balanceData ? (
                <>
                  {/* Section 1: Comparison */}
                  <div>
                    <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">Capacity Comparison</h4>
                    <div className="grid grid-cols-1 gap-3">
                      {balanceData.comparison.map((comp, cIdx) => {
                        const origPct = Math.round(comp.original_allocation * 100)
                        const balPct = Math.round(comp.balanced_allocation * 100)
                        const isReduction = balPct < origPct
                        const isIncrease = balPct > origPct

                        return (
                          <div key={cIdx} className="p-4 rounded-xl border border-slate-100 bg-slate-50/30 flex flex-col md:flex-row md:items-center justify-between gap-4">
                            <div className="space-y-1">
                              <span className="font-bold text-slate-800 text-sm">{comp.person}</span>
                              <div className="flex items-center gap-1.5">
                                <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${isReduction ? 'bg-rose-50 text-rose-700 border border-rose-100' : isIncrease ? 'bg-emerald-50 text-emerald-700 border border-emerald-100' : 'bg-slate-100 text-slate-600'}`}>
                                  {isReduction ? 'Optimized' : isIncrease ? 'Utilized' : 'Unchanged'}
                                </span>
                              </div>
                            </div>

                            <div className="flex items-center gap-6 shrink-0">
                              <div className="text-right">
                                <span className="block text-[10px] uppercase font-bold text-slate-400">Original</span>
                                <span className="text-sm font-semibold text-slate-700">{origPct}%</span>
                              </div>
                              <div className="text-slate-300">
                                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                                </svg>
                              </div>
                              <div className="text-right">
                                <span className="block text-[10px] uppercase font-bold text-indigo-400">Balanced</span>
                                <span className="text-sm font-bold text-indigo-600">{balPct}%</span>
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* Section 2: Planned Redistributions */}
                  <div>
                    <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">Planned Redistributions</h4>
                    <div className="space-y-3">
                      {balanceData.redistributions.length === 0 ? (
                        <p className="text-sm text-slate-500 italic">No redistributions planned.</p>
                      ) : (
                        balanceData.redistributions.map((red, rIdx) => (
                          <div key={rIdx} className="p-4 rounded-xl border border-slate-100 bg-slate-50/30 flex items-center justify-between gap-4">
                            <div className="space-y-1">
                              <div className="text-sm font-bold text-slate-800">{red.to_person} <span className="text-xs text-slate-400 font-normal">(from {red.from_person})</span></div>
                              {red.project && (
                                <div className="text-xs text-slate-500 font-medium">
                                  Project: <span className="font-mono text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded text-[10px]">{red.project}</span>
                                </div>
                              )}
                            </div>
                            <div className="shrink-0 text-right">
                              <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-indigo-50 text-indigo-700 border border-indigo-100">
                                {red.amount} FTE
                              </span>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </>
              ) : null}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 border-t border-slate-100 bg-slate-50/50 flex justify-end gap-3 shrink-0">
              <button
                onClick={() => setIsModalOpen(false)}
                className="cursor-pointer px-4 py-2 rounded-lg text-xs font-semibold bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 transition-colors shadow-sm"
              >
                Close
              </button>
            </div>

          </div>
        </div>
      )}
    </div>
  )
}
