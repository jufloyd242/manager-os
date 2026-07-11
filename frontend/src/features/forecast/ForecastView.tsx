import { useState, useEffect, useCallback } from 'react'
import { getForecast, postRefresh } from '../../api/client'
import type { ForecastResponse } from '../../api/client'

export function ForecastView() {
  const [data, setData] = useState<ForecastResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedWeek, setSelectedWeek] = useState<string | null>(null)
  const [exceptionsOnly, setExceptionsOnly] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getForecast({ week_start: selectedWeek || undefined, exceptions_only: exceptionsOnly || undefined, limit: 200 })
      setData(result.data)
      if (!selectedWeek && result.data.selected_week) {
        setSelectedWeek(result.data.selected_week)
      }
    } catch {
      setError('Failed to load forecast')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [selectedWeek, exceptionsOnly])

  useEffect(() => { load() }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    await postRefresh({ sources: ['forecast'], run_extraction: true })
    await load()
    setRefreshing(false)
  }

  const weekBack = () => {
    if (!selectedWeek || !data?.available_weeks) return
    const idx = data.available_weeks.indexOf(selectedWeek)
    if (idx > 0) {
      setSelectedWeek(data.available_weeks[idx - 1])
    }
  }

  const weekForward = () => {
    if (!data?.available_weeks) return
    const idx = data.available_weeks.indexOf(selectedWeek || '')
    if (idx < data.available_weeks.length - 1) {
      setSelectedWeek(data.available_weeks[idx + 1])
    }
  }

  const allocationClass = (pct: number) => {
    if (pct > 100.01) return 'text-red-600 font-semibold'
    if (pct < 80) return 'text-amber-600'
    return 'text-green-600'
  }

  const statusBadge = (classification: string) => {
    const colors: Record<string, string> = {
      overallocated: 'bg-red-100 text-red-800 border-red-200',
      underutilized: 'bg-amber-100 text-amber-800 border-amber-200',
      available: 'bg-blue-100 text-blue-800 border-blue-200',
      unknown: 'bg-slate-100 text-slate-500 border-slate-200',
      normal: 'bg-green-100 text-green-800 border-green-200',
    }
    return colors[classification] || colors.unknown
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Forecast</h1>
          {data && (
            <p className="text-sm text-slate-500">
              {data.person_count} people · {data.exception_count} exceptions · freshness: {data.freshness}
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

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <button
            onClick={weekBack}
            disabled={!data?.available_weeks?.length}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
          >
            ← Previous
          </button>
          <span className="text-sm font-medium text-slate-700">
            {selectedWeek ? `Week of ${selectedWeek}` : 'No week selected'}
          </span>
          <button
            onClick={weekForward}
            disabled={!data?.available_weeks || data.available_weeks.indexOf(selectedWeek || '') >= data.available_weeks.length - 1}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
          >
            Next →
          </button>
        </div>
        {data?.selection_explanation && (
          <span className="text-xs text-slate-400">{data.selection_explanation}</span>
        )}
        <label className="flex items-center gap-2 text-sm text-slate-600 ml-auto">
          <input
            type="checkbox"
            checked={exceptionsOnly}
            onChange={(e) => setExceptionsOnly(e.target.checked)}
            className="rounded border-slate-300"
          />
          Exceptions only
        </label>
      </div>

      {data?.status_counts && Object.keys(data.status_counts).length > 0 && (
        <div className="flex gap-4 text-sm">
          {Object.entries(data.status_counts).map(([key, count]) => (
            <div key={key} className="rounded-lg border border-slate-200 bg-white px-4 py-2">
              <span className="font-medium text-slate-900">{count}</span>
              <span className="ml-1 text-slate-500 capitalize">{key}</span>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="text-sm text-slate-400">Loading...</div>
      ) : data && data.people.length > 0 ? (
        <div className="space-y-2">
          <div className="grid grid-cols-12 gap-4 rounded-lg bg-slate-50 px-4 py-2 text-xs font-medium uppercase text-slate-500">
            <div className="col-span-3">Person</div>
            <div className="col-span-2">Allocation</div>
            <div className="col-span-2">Planned</div>
            <div className="col-span-2">Target</div>
            <div className="col-span-3">Status</div>
          </div>
          {data.people.map((p) => (
            <div key={p.person_name} className="grid grid-cols-12 gap-4 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm items-center">
              <div className="col-span-3 font-medium text-slate-900">{p.person_name}</div>
              <div className="col-span-2">
                <span className={allocationClass(p.allocation_pct)}>{p.allocation_pct.toFixed(1)}%</span>
              </div>
              <div className="col-span-2 text-slate-600">{p.planned_hours.toFixed(1)}h</div>
              <div className="col-span-2 text-slate-600">{p.target_hours ? `${p.target_hours}h` : '—'}</div>
              <div className="col-span-3">
                <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusBadge(p.classification)}`}>
                  {p.classification}
                </span>
                {p.warning && (
                  <span className="ml-2 text-xs text-amber-600">{p.warning}</span>
                )}
                {p.roll_off && (
                  <span className="ml-2 text-xs text-blue-600" title={p.roll_off.reason}>
                    Roll-off {p.roll_off.week}
                  </span>
                )}
              </div>
            </div>
          ))}
          {data.warnings.length > 0 && (
            <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
              {data.warnings.join(' · ')}
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-8 text-center text-sm text-slate-400">
          No forecast data found. Refresh from configured file.
        </div>
      )}
    </div>
  )
}