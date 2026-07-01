import type { RunRecord } from '../api/client'

const STATUS_STYLES: Record<RunRecord['status'], string> = {
  success: 'text-emerald-600',
  failed: 'text-red-600',
  running: 'text-blue-600',
  skipped: 'text-slate-400',
}

export function RecentRuns({ runs }: { runs: RunRecord[] }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-700">Recent Runs</h3>
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
            <tr key={run.run_id} className="border-t border-slate-100">
              <td className="py-1.5 text-slate-700">{run.command_id}</td>
              <td className={`py-1.5 font-semibold ${STATUS_STYLES[run.status]}`}>{run.status}</td>
              <td className="py-1.5 text-slate-500">{run.dry_run ? 'dry-run' : 'live'}</td>
              <td className="py-1.5 text-slate-500">{new Date(run.started_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
