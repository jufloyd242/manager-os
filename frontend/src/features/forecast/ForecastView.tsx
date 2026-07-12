import { useState, useEffect, useCallback } from 'react'
import { getForecast, postRefresh } from '../../api/client'
import type { ForecastResponse, ForecastPersonEntry } from '../../api/client'

function allocationClass(pct: number): string {
  if (pct > 100.01) return 'text-red-600 font-semibold'
  if (pct < 80) return 'text-amber-600'
  return 'text-green-600'
}

function statusBadge(classification: string): string {
  const colors: Record<string, string> = {
    overallocated: 'bg-red-100 text-red-800 border-red-200',
    underutilized: 'bg-amber-100 text-amber-800 border-amber-200',
    available: 'bg-blue-100 text-blue-800 border-blue-200',
    unknown: 'bg-slate-100 text-slate-500 border-slate-200',
    normal: 'bg-green-100 text-green-800 border-green-200',
  }
  return colors[classification] || colors.unknown
}

export function ForecastView() {
  const [data, setData] = useState<ForecastResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedWeek, setSelectedWeek] = useState<string | null>(null)
  const [personSearch, setPersonSearch] = useState('')
  const [exceptionsOnly, setExceptionsOnly] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getForecast({
        week_start: selectedWeek || undefined,
        exceptions_only: exceptionsOnly || undefined,
        limit: 200,
      })
      setData(result.data)
      if (!selectedWeek && result.data.selected_week) {
        setSelectedWeek(result.data.selected_week)
      }
    } catch {
      setError('Failed to load forecast')
    } finally {
      setLoading(false)
    }
  }, [selectedWeek, exceptionsOnly])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    setRefreshError(null)
    try {
      await postRefresh({ sources: ['forecast'], run_extraction: true })
      await load()
    } catch {
      setRefreshError('Refresh failed — displaying previous data')
    } finally {
      setRefreshing(false)
    }
  }

  const weekBack = () => {
    if (!selectedWeek || !data?.available_weeks) return
    const idx = data.available_weeks.indexOf(selectedWeek)
    if (idx > 0) setSelectedWeek(data.available_weeks[idx - 1])
  }

  const weekForward = () => {
    if (!data?.available_weeks) return
    const idx = data.available_weeks.indexOf(selectedWeek || '')
    if (idx < data.available_weeks.length - 1) {
      setSelectedWeek(data.available_weeks[idx + 1])
    }
  }

  const people = data?.people || []
  const filteredPeople = personSearch
    ? people.filter(p => p.person_name.toLowerCase().includes(personSearch.toLowerCase()))
    : people

  const selectedPersonData: ForecastPersonEntry | null = selectedPerson
    ? people.find(p => p.person_name === selectedPerson) || null
    : null

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-slate-200 bg-white flex-wrap">
        <div className="flex items-center gap-2">
          <button
            onClick={weekBack}
            disabled={!data?.available_weeks?.length}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
          >
            ← Prev
          </button>
          <select
            value={selectedWeek || ''}
            onChange={(e) => setSelectedWeek(e.target.value || null)}
            className="rounded border border-slate-300 px-2 py-1.5 text-sm"
          >
            {data?.available_weeks?.map(w => (
              <option key={w} value={w}>Week of {w}</option>
            ))}
          </select>
          <button
            onClick={weekForward}
            disabled={!data?.available_weeks || data.available_weeks.indexOf(selectedWeek || '') >= data.available_weeks.length - 1}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
          >
            Next →
          </button>
        </div>

        <input
          type="text"
          value={personSearch}
          onChange={(e) => setPersonSearch(e.target.value)}
          placeholder="Search people..."
          className="flex-1 min-w-[120px] rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
        />

        <div className="flex items-center gap-2">
          <button
            onClick={() => setExceptionsOnly(!exceptionsOnly)}
            className={`rounded-full border px-3 py-1 text-xs font-medium cursor-pointer ${
              exceptionsOnly
                ? 'bg-amber-100 text-amber-800 border-amber-300'
                : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
            }`}
          >
            Exceptions only
          </button>
        </div>

        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 cursor-pointer shrink-0"
        >
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* Freshness */}
      <div className="shrink-0 px-4 py-1.5 text-xs text-slate-500 bg-slate-50 border-b border-slate-200">
        {data && (
          <span>
            {data.person_count} people · {data.exception_count} exceptions · freshness: {data.freshness}
          </span>
        )}
        {refreshError && <span className="text-amber-600 ml-2">⚠ {refreshError}</span>}
      </div>

      {/* Master-detail */}
      <div className="flex-1 flex overflow-hidden">
        {/* People table */}
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {loading ? (
            <div className="p-8 text-center text-sm text-slate-400">Loading...</div>
          ) : error ? (
            <div className="p-8 text-center text-sm text-red-600">{error}</div>
          ) : filteredPeople.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-400">No forecast data found.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white border-b border-slate-200 z-10">
                <tr className="text-xs font-medium uppercase text-slate-500">
                  <th className="text-left px-4 py-2">Person</th>
                  <th className="text-right px-4 py-2">Alloc</th>
                  <th className="text-right px-4 py-2">Planned</th>
                  <th className="text-right px-4 py-2">Target</th>
                  <th className="text-left px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredPeople.map((p) => {
                  const isSelected = selectedPerson === p.person_name
                  return (
                    <tr
                      key={p.person_name}
                      onClick={() => setSelectedPerson(isSelected ? null : p.person_name)}
                      className={`cursor-pointer hover:bg-slate-50 ${isSelected ? 'bg-indigo-50' : ''}`}
                    >
                      <td className="px-4 py-2 font-medium text-slate-900 truncate max-w-[120px]">{p.person_name}</td>
                      <td className={`px-4 py-2 text-right ${allocationClass(p.allocation_pct)}`}>
                        {p.allocation_pct.toFixed(0)}%
                      </td>
                      <td className="px-4 py-2 text-right text-slate-600">{p.planned_hours.toFixed(0)}h</td>
                      <td className="px-4 py-2 text-right text-slate-600">{p.target_hours ? `${p.target_hours}h` : '—'}</td>
                      <td className="px-4 py-2">
                        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusBadge(p.classification)}`}>
                          {p.classification}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          {data?.warnings && data.warnings.length > 0 && (
            <div className="m-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">
              {data.warnings.join(' · ')}
            </div>
          )}
        </div>

        {/* Person detail */}
        <div className="w-1/2 overflow-y-auto p-6">
          {!selectedPersonData ? (
            <div className="text-center text-sm text-slate-400 py-8">Select a person to see details.</div>
          ) : (
            <div className="space-y-4">
              <div>
                <h2 className="text-lg font-bold text-slate-900">{selectedPersonData.person_name}</h2>
                <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium mt-1 ${statusBadge(selectedPersonData.classification)}`}>
                  {selectedPersonData.classification}
                </span>
              </div>

              {/* Allocation bar */}
              <div>
                <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
                  <span>Allocation</span>
                  <span className={allocationClass(selectedPersonData.allocation_pct)}>
                    {selectedPersonData.allocation_pct.toFixed(1)}%
                  </span>
                </div>
                <div className="h-3 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      selectedPersonData.allocation_pct > 100 ? 'bg-red-500' :
                      selectedPersonData.allocation_pct < 80 ? 'bg-amber-500' : 'bg-green-500'
                    }`}
                    style={{ width: `${Math.min(100, selectedPersonData.allocation_pct)}%` }}
                  />
                </div>
              </div>

              <dl className="text-sm space-y-1.5">
                <div className="flex justify-between">
                  <dt className="text-slate-400">Planned Hours</dt>
                  <dd className="text-slate-700">{selectedPersonData.planned_hours.toFixed(1)}h</dd>
                </div>
                {selectedPersonData.target_hours != null && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Target Hours</dt>
                    <dd className="text-slate-700">{selectedPersonData.target_hours}h</dd>
                  </div>
                )}
              </dl>

              {selectedPersonData.projects.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-1">Projects</h3>
                  <div className="space-y-1">
                    {selectedPersonData.projects.map((proj, i) => (
                      <p key={i} className="text-sm text-slate-600">{proj}</p>
                    ))}
                  </div>
                </div>
              )}

              {selectedPersonData.roll_off && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-700 mb-1">Roll-off</h3>
                  <p className="text-sm text-slate-600">
                    Week: {selectedPersonData.roll_off.week}
                  </p>
                  <p className="text-xs text-slate-400">{selectedPersonData.roll_off.reason}</p>
                </div>
              )}

              {selectedPersonData.warning && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  {selectedPersonData.warning}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
