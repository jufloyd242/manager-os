import { useEffect, useState } from 'react'
import type { CommandSpec, RunRecord, TokenEstimate, ValidateResponse, RunResponse } from '../api/client'
import { getCommands, validateCommand, runCommand } from '../api/client'
import { mockCommandRegistry } from '../api/mockData'
import { RiskBadge } from './RiskBadge'
import { LiveSingleFetchFlow } from './LiveSingleFetchFlow'

// The one command_id that gets the guarded multi-step external-call flow in
// this pass. project_docs_fetch_batch_live_bounded and any risk_level=blocked
// command must NOT get this treatment — they keep the prior phase's plain
// disabled treatment below.
const LIVE_SINGLE_FETCH_COMMAND_ID = 'project_docs_fetch_live_single'

export interface CommandCenterProps {
  onRunRecorded?: (run: RunRecord) => void
  onEstimate?: (estimate: TokenEstimate | null) => void
}

function isRunEnabled(command: CommandSpec): boolean {
  return command.risk_level === 'local_safe' && !command.requires_confirmation
}

function coerceParams(command: CommandSpec, raw: Record<string, string>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const param of command.parameters) {
    const value = raw[param.name]
    if (value === undefined || value === '') continue
    if (param.type === 'int') result[param.name] = Number.parseInt(value, 10)
    else if (param.type === 'float') result[param.name] = Number.parseFloat(value)
    else if (param.type === 'bool') result[param.name] = value === 'true'
    else result[param.name] = value
  }
  return result
}

function CommandRow({
  command,
  onRunRecorded,
  onEstimate,
}: {
  command: CommandSpec
  onRunRecorded: (run: RunRecord) => void
  onEstimate: (estimate: TokenEstimate | null) => void
}) {
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [validateResult, setValidateResult] = useState<ValidateResponse | null>(null)
  const [validateMock, setValidateMock] = useState(false)
  const [runResult, setRunResult] = useState<RunResponse | null>(null)
  const [runMock, setRunMock] = useState(false)
  const [busy, setBusy] = useState(false)

  const blocked = command.risk_level === 'blocked'
  const runEnabled = isRunEnabled(command)
  const notRunnableYet = !runEnabled
  const isLiveSingleFetch = command.command_id === LIVE_SINGLE_FETCH_COMMAND_ID

  function setParam(name: string, value: string) {
    setParamValues((prev) => ({ ...prev, [name]: value }))
  }

  async function handleValidate() {
    if (blocked) return
    setBusy(true)
    try {
      const result = await validateCommand(command.command_id, coerceParams(command, paramValues))
      setValidateResult(result.data)
      setValidateMock(result.isMock)
      onEstimate({
        command_id: command.command_id,
        label: command.label,
        risk_level: command.risk_level,
        estimated_input_tokens: result.data.estimated_input_tokens,
      })
    } finally {
      setBusy(false)
    }
  }

  async function handleRun() {
    if (!runEnabled) return
    setBusy(true)
    try {
      const result = await runCommand(command.command_id, coerceParams(command, paramValues), false)
      setRunResult(result.data)
      setRunMock(result.isMock)
      onEstimate({
        command_id: command.command_id,
        label: command.label,
        risk_level: command.risk_level,
        estimated_input_tokens: result.data.estimated_input_tokens,
      })
      onRunRecorded({
        run_id: result.data.run_id,
        command_id: result.data.command_id,
        status: (result.data.status as RunRecord['status']) ?? 'success',
        dry_run: false,
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        stdout: result.data.stdout,
        stderr: result.data.stderr,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 p-3" data-testid={`command-row-${command.command_id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-800">{command.label}</p>
          <p className="text-xs text-slate-500">{command.description}</p>
        </div>
        <RiskBadge level={command.risk_level} />
      </div>

      {blocked && (
        <p className="mt-2 text-xs font-medium text-red-600">
          Blocked — not runnable in this phase. This command cannot be run from the command tower.
        </p>
      )}

      {!blocked && !isLiveSingleFetch && notRunnableYet && (
        <p className="mt-2 text-xs font-medium text-amber-700">
          Requires confirmation — not runnable in this phase (external call risk: {command.external_call_risk}).
        </p>
      )}

      {!blocked && isLiveSingleFetch && (
        <LiveSingleFetchFlow
          command={command}
          onRunRecorded={onRunRecorded}
          onEstimate={onEstimate}
        />
      )}

      {!isLiveSingleFetch && (
        <>
          {command.parameters.length > 0 && (
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {command.parameters.map((param) => (
                <label key={param.name} className="text-xs text-slate-600">
                  {param.name}
                  {param.required ? ' *' : ''}
                  <input
                    type="text"
                    value={paramValues[param.name] ?? ''}
                    onChange={(e) => setParam(param.name, e.target.value)}
                    placeholder={param.help}
                    aria-label={`${command.label} parameter ${param.name}`}
                    className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs"
                  />
                </label>
              ))}
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={blocked || busy}
              onClick={handleValidate}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Validate
            </button>
            <button
              type="button"
              disabled={runDisabledForDryRun(command) || busy}
              onClick={handleValidate}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Dry Run
            </button>
            {command.supports_print_prompt && (
              <button
                type="button"
                disabled={blocked || busy}
                onClick={handleValidate}
                className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Print Prompt
              </button>
            )}
            <button
              type="button"
              disabled={!runEnabled || busy}
              onClick={handleRun}
              title={
                blocked
                  ? 'This command is blocked'
                  : notRunnableYet
                    ? 'Requires confirmation — not runnable in this phase'
                    : undefined
              }
              className={`rounded-md px-2.5 py-1 text-xs font-semibold text-white ${
                !runEnabled || busy ? 'cursor-not-allowed bg-slate-300' : 'bg-slate-900 hover:bg-slate-700'
              }`}
            >
              Run
            </button>
          </div>

          {validateResult && (
            <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
              {validateMock && (
                <p className="mb-1 font-semibold text-amber-700" data-testid="validate-mock-indicator">
                  Offline / Mock Data — validation simulated, no real backend call.
                </p>
              )}
              <p>
                <span className="font-semibold">argv preview:</span>{' '}
                {validateResult.argv_preview ? validateResult.argv_preview.join(' ') : 'n/a (blocked)'}
              </p>
              <p>
                <span className="font-semibold">estimated input tokens:</span>{' '}
                {validateResult.estimated_input_tokens ?? 'n/a'}
              </p>
              {validateResult.warnings.length > 0 && (
                <p className="mt-1 text-amber-700">{validateResult.warnings.join(' · ')}</p>
              )}
            </div>
          )}

          {runResult && (
            <div className="mt-3 rounded-md border border-slate-200 bg-slate-900 p-2 text-xs text-slate-100">
              {runMock && (
                <p className="mb-1 font-semibold text-amber-300" data-testid="run-mock-indicator">
                  Offline / Mock Data — run simulated, no real backend call.
                </p>
              )}
              {runResult.stdout && <pre className="whitespace-pre-wrap">{runResult.stdout}</pre>}
              {runResult.stderr && <pre className="whitespace-pre-wrap text-red-300">{runResult.stderr}</pre>}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function runDisabledForDryRun(command: CommandSpec): boolean {
  return command.risk_level === 'blocked' || !command.supports_dry_run
}

export function CommandCenter({ onRunRecorded, onEstimate }: CommandCenterProps) {
  const [commands, setCommands] = useState<CommandSpec[]>([])
  const [isMock, setIsMock] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getCommands()
      .then((result) => {
        if (cancelled) return
        setCommands(result.data)
        setIsMock(result.isMock)
      })
      .catch(() => {
        if (cancelled) return
        setCommands(mockCommandRegistry)
        setIsMock(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">Command Center</h3>
        {isMock && (
          <span
            data-testid="command-center-mock-indicator"
            className="rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
          >
            Offline / Mock Data
          </span>
        )}
      </div>
      <div className="mt-3 space-y-3">
        {loading && <p className="text-xs text-slate-400">Loading commands…</p>}
        {commands.map((command) => (
          <CommandRow
            key={command.command_id}
            command={command}
            onRunRecorded={onRunRecorded ?? (() => {})}
            onEstimate={onEstimate ?? (() => {})}
          />
        ))}
      </div>
    </div>
  )
}

