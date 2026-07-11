import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { EmptyState } from '../components/primitives/EmptyState'
import { getProjects } from '../api/client'
import type { ProjectsResponse } from '../api/client'

export function ProjectsPage() {
  const [data, setData] = useState<ProjectsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedProject, setSelectedProject] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getProjects()
      setData(result.data)
    } catch {
      setError('Failed to load projects')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  const projects = data?.projects || []
  const selected = selectedProject !== null ? projects[selectedProject] : null

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Projects" description="What is the current state of each project?" />
      <div className="flex-1 flex overflow-hidden">
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {projects.length === 0 ? (
            <EmptyState message="No projects available." />
          ) : (
            projects.map((p, i) => {
              const name = String(p.name || p.project_name || p.opportunity_number || `Project ${i + 1}`)
              const client = String(p.client || '')
              return (
                <button
                  key={i}
                  onClick={() => setSelectedProject(i)}
                  className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${
                    selectedProject === i ? 'bg-indigo-50' : ''
                  }`}
                >
                  <p className="text-sm font-medium text-slate-900">{name}</p>
                  {client && <p className="text-xs text-slate-500">{client}</p>}
                </button>
              )
            })
          )}
        </div>
        <div className="w-1/2 overflow-y-auto p-6">
          {!selected ? (
            <EmptyState message="Select a project to see details." />
          ) : (
            <div className="space-y-2">
              <h2 className="text-lg font-bold text-slate-900">
                {String(selected.name || selected.project_name || 'Project')}
              </h2>
              <dl className="text-sm space-y-2">
                {Object.entries(selected).map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-slate-400 text-xs">{key.replace(/_/g, ' ')}</dt>
                    <dd className="text-slate-700">{String(value ?? '—')}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
