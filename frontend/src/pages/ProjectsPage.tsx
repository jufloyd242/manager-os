import { useEffect, useState, useCallback } from 'react'
import { PageHeader } from '../components/PageHeader'
import { LoadingState } from '../components/primitives/LoadingState'
import { ErrorState } from '../components/primitives/ErrorState'
import { EmptyState } from '../components/primitives/EmptyState'
import { StatusBadge } from '../components/primitives/StatusBadge'
import {
  getProjects,
  getProjectDocuments,
  validateCommand,
  runCommand,
} from '../api/client'
import type {
  ProjectsResponse,
  ProjectDocumentEntry,
  RunRecord,
} from '../api/client'

interface ProjectRow {
  id: string
  project_name: string | null
  client: string | null
  opportunity_number: string | null
  status: string | null
  close_date: string | null
  services_amount: number | null
  sales_rep: string | null
  project_type: string | null
  industry: string | null
  short_description: string | null
  summary: string | null
  year: number | null
  month: number | null
}

interface ProjectsPageProps {
  onRunRecorded: (run: RunRecord) => void
}

const DRY_RUN_COMMAND_ID = 'project_docs_fetch_dry_run'
const PRINT_PROMPT_COMMAND_ID = 'project_docs_fetch_print_prompt'
const LIVE_COMMAND_ID = 'project_docs_fetch_live_single'

export function ProjectsPage({ onRunRecorded }: ProjectsPageProps) {
  const [data, setData] = useState<ProjectsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedProject, setSelectedProject] = useState<number | null>(null)
  const [documents, setDocuments] = useState<ProjectDocumentEntry[]>([])
  const [docsLoading, setDocsLoading] = useState(false)
  const [docsError, setDocsError] = useState<string | null>(null)
  const [dryRunOutput, setDryRunOutput] = useState<string | null>(null)
  const [liveStatus, setLiveStatus] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

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

  const projects = (data?.projects || []) as unknown as ProjectRow[]
  const selected = selectedProject !== null ? projects[selectedProject] : null

  const loadDocuments = useCallback(async (opp: string) => {
    setDocsLoading(true)
    setDocsError(null)
    try {
      const result = await getProjectDocuments(opp)
      setDocuments(result.data.documents)
    } catch {
      setDocsError('Failed to load documents')
      setDocuments([])
    } finally {
      setDocsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected?.opportunity_number) {
      loadDocuments(selected.opportunity_number)
      setDryRunOutput(null)
      setLiveStatus(null)
    } else {
      setDocuments([])
    }
  }, [selected?.opportunity_number, loadDocuments])

  const handleDryRun = async () => {
    if (!selected?.opportunity_number) return
    setActionLoading(true)
    setDryRunOutput(null)
    try {
      const result = await validateCommand(DRY_RUN_COMMAND_ID, {
        opportunity_number: selected.opportunity_number,
      })
      if (result.data.argv_preview) {
        setDryRunOutput(result.data.argv_preview.join(' '))
      } else {
        setDryRunOutput('Dry run validated (no preview available)')
      }
    } catch {
      setDryRunOutput('Dry run failed')
    } finally {
      setActionLoading(false)
    }
  }

  const handlePrintPrompt = async () => {
    if (!selected?.opportunity_number) return
    setActionLoading(true)
    setDryRunOutput(null)
    try {
      const result = await runCommand(PRINT_PROMPT_COMMAND_ID, {
        opportunity_number: selected.opportunity_number,
      })
      if (result.data.ok && result.data.stdout) {
        setDryRunOutput(result.data.stdout)
      } else {
        setDryRunOutput(result.data.error || 'Print prompt failed')
      }
      if (result.data.run_id) {
        onRunRecorded({
          run_id: result.data.run_id,
          command_id: result.data.command_id,
          status: result.data.status as RunRecord['status'],
          dry_run: false,
          started_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
        })
      }
    } catch {
      setDryRunOutput('Print prompt failed')
    } finally {
      setActionLoading(false)
    }
  }

  const handleLiveFetch = async () => {
    if (!selected?.opportunity_number) return
    setActionLoading(true)
    setLiveStatus(null)
    try {
      const result = await runCommand(
        LIVE_COMMAND_ID,
        {
          opportunity_number: selected.opportunity_number,
          limit: 3,
          timeout: 60,
        },
        true, // confirm
      )
      if (result.data.ok) {
        setLiveStatus(`Live fetch complete: ${result.data.status}`)
        await loadDocuments(selected.opportunity_number)
      } else {
        setLiveStatus(`Live fetch: ${result.data.error || result.data.status}`)
      }
      if (result.data.run_id) {
        onRunRecorded({
          run_id: result.data.run_id,
          command_id: result.data.command_id,
          status: result.data.status as RunRecord['status'],
          dry_run: false,
          started_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
        })
      }
    } catch {
      setLiveStatus('Live fetch failed')
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Projects" description="What is the current state of each project?" />
      <div className="flex-1 flex overflow-hidden">
        {/* Project list */}
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          {projects.length === 0 ? (
            <EmptyState message="No projects available." />
          ) : (
            projects.map((p, i) => {
              const name = String(p.project_name || p.opportunity_number || `Project ${i + 1}`)
              const client = String(p.client || '')
              const opp = String(p.opportunity_number || '')
              return (
                <button
                  key={p.id || opp || i}
                  onClick={() => setSelectedProject(i)}
                  className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${
                    selectedProject === i ? 'bg-indigo-50' : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-900 truncate">{name}</p>
                    {opp && <span className="text-xs text-slate-400 ml-2 shrink-0">{opp}</span>}
                  </div>
                  {client && <p className="text-xs text-slate-500 truncate">{client}</p>}
                </button>
              )
            })
          )}
        </div>

        {/* Project detail */}
        <div className="w-1/2 overflow-y-auto p-6">
          {!selected ? (
            <EmptyState message="Select a project to see details." />
          ) : (
            <div className="space-y-4">
              <div>
                <h2 className="text-lg font-bold text-slate-900">
                  {String(selected.project_name || 'Project')}
                </h2>
                {selected.opportunity_number && (
                  <p className="text-sm text-slate-500">
                    OPP: <span className="font-mono">{selected.opportunity_number}</span>
                  </p>
                )}
                {selected.client && (
                  <p className="text-sm text-slate-500">{String(selected.client)}</p>
                )}
              </div>

              {/* Project metadata */}
              <dl className="text-sm space-y-1.5">
                {selected.status && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Status</dt>
                    <dd className="text-slate-700">{String(selected.status)}</dd>
                  </div>
                )}
                {selected.close_date && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Close Date</dt>
                    <dd className="text-slate-700">{String(selected.close_date)}</dd>
                  </div>
                )}
                {selected.services_amount != null && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Services Amount</dt>
                    <dd className="text-slate-700">${Number(selected.services_amount).toLocaleString()}</dd>
                  </div>
                )}
                {selected.sales_rep && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Sales Rep</dt>
                    <dd className="text-slate-700">{String(selected.sales_rep)}</dd>
                  </div>
                )}
                {selected.project_type && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Project Type</dt>
                    <dd className="text-slate-700">{String(selected.project_type)}</dd>
                  </div>
                )}
                {selected.industry && (
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Industry</dt>
                    <dd className="text-slate-700">{String(selected.industry)}</dd>
                  </div>
                )}
              </dl>

              {selected.short_description && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-1">Description</h3>
                  <p className="text-sm text-slate-600">{String(selected.short_description)}</p>
                </div>
              )}

              {selected.summary && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-1">Summary</h3>
                  <p className="text-sm text-slate-600">{String(selected.summary)}</p>
                </div>
              )}

              {/* Document retrieval */}
              {selected.opportunity_number && (
                <div className="border-t border-slate-200 pt-4">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-slate-700">Documents</h3>
                    <div className="flex gap-2">
                      <button
                        onClick={handleDryRun}
                        disabled={actionLoading}
                        className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
                      >
                        Dry Run
                      </button>
                      <button
                        onClick={handlePrintPrompt}
                        disabled={actionLoading}
                        className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
                      >
                        Print Prompt
                      </button>
                      <button
                        onClick={handleLiveFetch}
                        disabled={actionLoading}
                        className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50 cursor-pointer"
                      >
                        Live Fetch
                      </button>
                    </div>
                  </div>

                  {actionLoading && (
                    <p className="text-xs text-slate-400">Processing...</p>
                  )}

                  {dryRunOutput && (
                    <details className="mb-2">
                      <summary className="text-xs text-slate-500 cursor-pointer">Query preview</summary>
                      <pre className="text-xs text-slate-600 bg-slate-50 rounded p-2 mt-1 overflow-x-auto whitespace-pre-wrap">{dryRunOutput}</pre>
                    </details>
                  )}

                  {liveStatus && (
                    <p className="text-xs text-slate-500 mb-2">{liveStatus}</p>
                  )}

                  {docsLoading ? (
                    <p className="text-xs text-slate-400">Loading documents...</p>
                  ) : docsError ? (
                    <p className="text-xs text-red-600">{docsError}</p>
                  ) : documents.length === 0 ? (
                    <p className="text-xs text-slate-400">No documents found. Use Dry Run to preview, then Live Fetch to retrieve.</p>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-xs text-slate-500">
                        Found {documents.length} document{documents.length !== 1 ? 's' : ''}:
                      </p>
                      {documents.map((doc) => (
                        <div key={doc.id} className="rounded-lg border border-slate-200 p-2">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-slate-700">
                              {doc.document_type || 'other'}
                            </span>
                            {doc.search_status && (
                              <StatusBadge status={doc.search_status} />
                            )}
                          </div>
                          <p className="text-sm text-slate-900 mt-1">{doc.title || 'Untitled'}</p>
                          {doc.why_matched && (
                            <p className="text-xs text-slate-400 mt-0.5">{doc.why_matched}</p>
                          )}
                          {doc.url && (
                            <a
                              href={doc.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs text-indigo-600 hover:underline mt-1 inline-block"
                            >
                              Open document →
                            </a>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
