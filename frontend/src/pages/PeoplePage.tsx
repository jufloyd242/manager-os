import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { EmptyState } from '../components/primitives/EmptyState'
import { getPeople } from '../api/client'
import type { PeopleResponse } from '../api/client'

export function PeoplePage() {
  const [data, setData] = useState<PeopleResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getPeople()
      setData(result.data)
    } catch {
      setError('Failed to load people data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  const people = data?.people || []
  const selected = people.find(p => p.id === selectedPerson)

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="People" description="What is the current state of each person?" />
      <div className="flex-1 flex overflow-hidden">
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {people.length === 0 ? (
            <EmptyState message="No people data available." />
          ) : (
            people.map(p => (
              <button
                key={p.id}
                onClick={() => setSelectedPerson(p.id)}
                className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${
                  selectedPerson === p.id ? 'bg-indigo-50' : ''
                }`}
              >
                <p className="text-sm font-medium text-slate-900">{p.name}</p>
                <p className="text-xs text-slate-500">{p.role || '—'}</p>
                {p.allocation_pct !== null && (
                  <p className="text-xs text-slate-400">{p.allocation_pct}% allocated</p>
                )}
              </button>
            ))
          )}
        </div>
        <div className="w-1/2 overflow-y-auto p-6">
          {!selected ? (
            <EmptyState message="Select a person to see details." />
          ) : (
            <div className="space-y-3">
              <h2 className="text-lg font-bold text-slate-900">{selected.name}</h2>
              <dl className="text-sm space-y-2">
                <div><dt className="text-slate-400">Role</dt><dd className="text-slate-700">{selected.role || '—'}</dd></div>
                <div><dt className="text-slate-400">Current Client</dt><dd className="text-slate-700">{selected.current_client || '—'}</dd></div>
                <div><dt className="text-slate-400">Allocation</dt><dd className="text-slate-700">{selected.allocation_pct !== null ? `${selected.allocation_pct}%` : '—'}</dd></div>
                <div><dt className="text-slate-400">Next Available</dt><dd className="text-slate-700">{selected.next_availability_date || '—'}</dd></div>
                <div><dt className="text-slate-400">Last 1:1</dt><dd className="text-slate-700">{selected.last_1on1_date || '—'}</dd></div>
                <div><dt className="text-slate-400">Morale Signal</dt><dd className="text-slate-700">{selected.morale_signal || '—'}</dd></div>
                <div><dt className="text-slate-400">Growth Topic</dt><dd className="text-slate-700">{selected.growth_topic || '—'}</dd></div>
                <div><dt className="text-slate-400">Blockers</dt><dd className="text-slate-700">{selected.blockers || '—'}</dd></div>
              </dl>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
