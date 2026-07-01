import { useState } from 'react'
import type { CommandDefinition } from '../api/client'
import { RiskBadge } from './RiskBadge'

export interface CommandCenterProps {
  commands: CommandDefinition[]
  onRun: (command: CommandDefinition, dryRun: boolean) => void
}

function needsConfirmation(command: CommandDefinition): boolean {
  return (
    command.requires_confirmation ||
    command.external_call_risk === 'likely' ||
    command.external_call_risk === 'high'
  )
}

function CommandRow({
  command,
  onRun,
}: {
  command: CommandDefinition
  onRun: CommandCenterProps['onRun']
}) {
  const [confirmed, setConfirmed] = useState(false)
  const blocked = command.risk_level === 'blocked'
  const requiresConfirm = needsConfirmation(command) && !blocked
  const runDisabled = blocked || (requiresConfirm && !confirmed)

  return (
    <div className="rounded-lg border border-slate-200 p-3" data-testid={`command-row-${command.command_id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-800">{command.label}</p>
          <p className="text-xs text-slate-500">{command.description}</p>
        </div>
        <RiskBadge level={command.risk_level} />
      </div>

      {requiresConfirm && (
        <label className="mt-2 flex items-center gap-2 text-xs font-medium text-amber-700">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            aria-label={`Confirm ${command.label}`}
          />
          Requires confirmation before running (external call risk: {command.external_call_risk})
        </label>
      )}

      {blocked && (
        <p className="mt-2 text-xs font-medium text-red-600">
          Blocked — this command cannot be run from the command tower.
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!command.supports_dry_run}
          onClick={() => onRun(command, true)}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Dry Run
        </button>
        <button
          type="button"
          onClick={() =>
            window.alert(`Prompt preview for "${command.label}" (${command.command_id}) — mock only.`)
          }
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          Print Prompt
        </button>
        <button
          type="button"
          disabled={runDisabled}
          onClick={() => onRun(command, false)}
          title={
            blocked
              ? 'This command is blocked'
              : requiresConfirm
                ? 'Check the confirmation box to enable Run'
                : undefined
          }
          className={`rounded-md px-2.5 py-1 text-xs font-semibold text-white ${
            runDisabled ? 'cursor-not-allowed bg-slate-300' : 'bg-slate-900 hover:bg-slate-700'
          }`}
        >
          Run
        </button>
        <button
          type="button"
          onClick={() => window.alert(`Logs for "${command.label}" (mock — no logs backend wired yet).`)}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          View Logs
        </button>
      </div>
    </div>
  )
}

export function CommandCenter({ commands, onRun }: CommandCenterProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-700">Command Center</h3>
      <div className="mt-3 space-y-3">
        {commands.map((command) => (
          <CommandRow key={command.command_id} command={command} onRun={onRun} />
        ))}
      </div>
    </div>
  )
}
