import { useEffect, useState } from 'react'
import type { RunRecord, RunStatus } from '../api/client'
import { getRuns, getRunLogs } from '../api/client'
import { mockRecentRuns } from '../api/mockData'

const STATUS_STYLES: Record<RunStatus, string> = {
  success: 'text-emerald-600',
  ok: 'text-emerald-600',
  failed: 'text-red-600',
  error: 'text-red-600',
  running: 'text-blue-600',
  skipped: 'text-slate-400',
  blocked: 'text-slate-400',
}

export interface RecentRunsProps {
  refreshKey?: number
}

export function RecentRuns({ refreshKey }: RecentRunsProps) {
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [isMock, setIsMock] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [logs, setLogs] = useState<{ stdout: string; stderr: string } | null>(null)
  const [logsMock, setLogsMock] = useState(false)

  useEffect(() => {
    let cancelled = false
    getRuns()
      .then((result) => {
        if (cancelled) return
        setRuns(result.data)
        setIsMock(result.isMock)
      })
      .catch(() => {
        if (cancelled) return
        setRuns(mockRecentRuns)
        setIsMock(true)
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  async function handleSelect(run: RunRecord) {
    setSelectedRunId(run.run_id)
    if (run.stdout != null || run.stderr != null) {
      setLogs({ stdout: run.stdout ?? '', stderr: run.stderr ?? '' })
      setLogsMock(false)
      return
    }
    try {
      const result = await getRunLogs(run.run_id)
      setLogs({ stdout: result.data.stdout ?? '', stderr: result.data.stderr ?? '' })
      setLogsMock(result.isMock)
    } catch {
      setLogs({ stdout: '', stderr: 'Failed to load logs.' })
      setLogsMock(true)
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">Recent Runs</h3>
        {isMock && (
          <span
            data-testid="recent-runs-mock-indicator"
            className="rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
          >
            Offline / Mock Data
          </span>
        )}
      </div>
      <table className="mt-3 w-full text-left text-xs">
        <thead className="text-slate-400">
          <tr>
            <th className="pb-1 font-medium">Command</th>
            <th className="pb-1 font-medium">Status</th>
            <th className="pb-1 font-medium">Mode</th>
            <th className="pb-1 font-medium">Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.run_id}
              className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${
                selectedRunId === run.run_id ? 'bg-slate-50' : ''
              }`}
              onClick={() => handleSelect(run)}
            >
              <td className="py-1.5 text-slate-700">{run.command_id}</td>
              <td className={`py-1.5 font-semibold ${STATUS_STYLES[run.status]}`}>{run.status}</td>
              <td className="py-1.5 text-slate-500">{run.dry_run ? 'dry-run' : 'live'}</td>
              <td className="py-1.5 text-slate-500">{new Date(run.started_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {logs && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-900 p-2 text-xs text-slate-100">
          {logsMock && (
            <p className="mb-1 font-semibold text-amber-300" data-testid="run-logs-mock-indicator">
              Offline / Mock Data — logs simulated, no real backend call.
            </p>
          )}
          {logs.stdout && <pre className="whitespace-pre-wrap">{logs.stdout}</pre>}
          {logs.stderr && <pre className="whitespace-pre-wrap text-red-300">{logs.stderr}</pre>}
        </div>
      )}
    </div>
  )
}

