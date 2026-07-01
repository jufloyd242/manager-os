import { useState } from 'react'
import type { CommandSpec, RunRecord, RunResponse, TokenEstimate, ValidateResponse } from '../api/client'
import { runCommand, validateCommand } from '../api/client'
import { ConfirmLiveRunToggle, DryRunRequiredMessage, ExternalCallWarning } from './GuardedExternalCallNotice'

// Fallback command id of the local, no-external-call dry-run companion
// command, used only if the command spec doesn't declare
// `related_dry_run_command` (kept for back-compat with older registries).
const FALLBACK_DRY_RUN_COMMAND_ID = 'project_docs_fetch_dry_run'

// Fallback numeric bounds, used only if the command spec's `parameters`
// don't declare a `default`/`maximum` for `limit`/`timeout` (kept for
// back-compat with older registries that predate these fields).
const FALLBACK_DEFAULT_LIMIT = 3
const FALLBACK_MAX_LIMIT = 5
const FALLBACK_DEFAULT_TIMEOUT = 60
const FALLBACK_MAX_TIMEOUT = 120

function numberOrFallback(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export interface LiveSingleFetchFlowProps {
  command: CommandSpec
  onRunRecorded: (run: RunRecord) => void
  onEstimate: (estimate: TokenEstimate | null) => void
}

/**
 * Guarded multi-step UX for `project_docs_fetch_live_single`
 * (risk_level=external_bounded, external_call_risk=likely,
 * requires_confirmation=true, dry_run_required_before_live=true).
 *
 * Flow: 1) Validate -> 2) Run Dry Run (project_docs_fetch_dry_run) ->
 * 3) Confirm ("I understand this will contact external services") ->
 * 4) Run Live (project_docs_fetch_live_single, confirm=true, params include
 * the dry run's run_id).
 */
export function LiveSingleFetchFlow({ command, onRunRecorded, onEstimate }: LiveSingleFetchFlowProps) {
  const dryRunCommandId = command.related_dry_run_command ?? FALLBACK_DRY_RUN_COMMAND_ID

  const limitParam = command.parameters.find((p) => p.name === 'limit')
  const timeoutParam = command.parameters.find((p) => p.name === 'timeout')
  const defaultLimit = numberOrFallback(limitParam?.default, FALLBACK_DEFAULT_LIMIT)
  const maxLimit = numberOrFallback(limitParam?.maximum, FALLBACK_MAX_LIMIT)
  const defaultTimeout = numberOrFallback(timeoutParam?.default, FALLBACK_DEFAULT_TIMEOUT)
  const maxTimeout = numberOrFallback(timeoutParam?.maximum, FALLBACK_MAX_TIMEOUT)

  const [opportunityNumber, setOpportunityNumber] = useState('')
  const [limitInput, setLimitInput] = useState(String(defaultLimit))
  const [timeoutInput, setTimeoutInput] = useState(String(defaultTimeout))

  const [validateResult, setValidateResult] = useState<ValidateResponse | null>(null)
  const [validateMock, setValidateMock] = useState(false)
  const [validateError, setValidateError] = useState<string | null>(null)

  const [dryRunResult, setDryRunResult] = useState<RunResponse | null>(null)
  const [dryRunRunId, setDryRunRunId] = useState<string | null>(null)
  const [dryRunError, setDryRunError] = useState<string | null>(null)

  const [confirmed, setConfirmed] = useState(false)

  const [liveRunResult, setLiveRunResult] = useState<RunResponse | null>(null)
  const [liveRunMock, setLiveRunMock] = useState(false)
  const [liveRunError, setLiveRunError] = useState<string | null>(null)

  const [busy, setBusy] = useState(false)

  const opportunityNumberValid = opportunityNumber.trim().length > 0
  const limitNumber = Number.parseInt(limitInput, 10)
  const timeoutNumber = Number.parseInt(timeoutInput, 10)
  const limitValid = Number.isFinite(limitNumber) && limitNumber > 0 && limitNumber <= maxLimit
  const timeoutValid = Number.isFinite(timeoutNumber) && timeoutNumber > 0 && timeoutNumber <= maxTimeout
  const formValid = opportunityNumberValid && limitValid && timeoutValid

  const validated = validateResult?.ok === true
  const dryRunDone = dryRunRunId != null
  const canRunLive = validated && dryRunDone && confirmed && formValid

  function buildParams(): Record<string, unknown> {
    return {
      opportunity_number: opportunityNumber.trim(),
      limit: limitNumber,
      timeout: timeoutNumber,
    }
  }

  async function handleValidate() {
    if (!formValid || busy) return
    setBusy(true)
    setValidateError(null)
    try {
      const result = await validateCommand(command.command_id, buildParams())
      setValidateResult(result.data)
      setValidateMock(result.isMock)
      onEstimate({
        command_id: command.command_id,
        label: command.label,
        risk_level: command.risk_level,
        estimated_input_tokens: result.data.estimated_input_tokens,
      })
    } catch {
      setValidateResult(null)
      setValidateError('Validation failed — could not reach the Manager OS API.')
    } finally {
      setBusy(false)
    }
  }

  async function handleDryRun() {
    if (!validated || !formValid || busy) return
    setBusy(true)
    setDryRunError(null)
    try {
      const result = await runCommand(dryRunCommandId, buildParams(), false)
      setDryRunResult(result.data)
      setDryRunRunId(result.data.run_id)
    } catch {
      setDryRunResult(null)
      setDryRunRunId(null)
      setDryRunError('Dry run failed — could not reach the Manager OS API.')
    } finally {
      setBusy(false)
    }
  }

  async function handleRunLive() {
    if (!canRunLive || busy || dryRunRunId == null) return
    setBusy(true)
    setLiveRunError(null)
    try {
      const result = await runCommand(command.command_id, { ...buildParams(), dry_run_run_id: dryRunRunId }, true)
      setLiveRunResult(result.data)
      setLiveRunMock(result.isMock)
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
    } catch {
      setLiveRunResult(null)
      setLiveRunError('Live run failed — could not reach the Manager OS API.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <ExternalCallWarning />

      {(command.related_dry_run_command || command.related_print_prompt_command) && (
        <p className="mt-1 text-xs text-slate-500" data-testid="related-commands">
          Related:{' '}
          {command.related_dry_run_command && `Run dry-run first (${command.related_dry_run_command})`}
          {command.related_dry_run_command && command.related_print_prompt_command && ' or '}
          {command.related_print_prompt_command && `preview the prompt (${command.related_print_prompt_command})`}.
        </p>
      )}

      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="text-xs text-slate-600">
          opportunity_number *
          <input
            type="text"
            value={opportunityNumber}
            onChange={(e) => setOpportunityNumber(e.target.value)}
            aria-label={`${command.label} parameter opportunity_number`}
            className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs"
          />
          {!opportunityNumberValid && (
            <span className="mt-0.5 block text-red-600" data-testid="opportunity-number-error">
              Opportunity number is required.
            </span>
          )}
        </label>
        <label className="text-xs text-slate-600">
          limit (max {maxLimit})
          <input
            type="number"
            value={limitInput}
            max={maxLimit}
            min={1}
            onChange={(e) => setLimitInput(e.target.value)}
            aria-label={`${command.label} parameter limit`}
            className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs"
          />
          {!limitValid && (
            <span className="mt-0.5 block text-red-600" data-testid="limit-error">
              Limit must be between 1 and {maxLimit}.
            </span>
          )}
        </label>
        <label className="text-xs text-slate-600">
          timeout (max {maxTimeout})
          <input
            type="number"
            value={timeoutInput}
            max={maxTimeout}
            min={1}
            onChange={(e) => setTimeoutInput(e.target.value)}
            aria-label={`${command.label} parameter timeout`}
            className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs"
          />
          {!timeoutValid && (
            <span className="mt-0.5 block text-red-600" data-testid="timeout-error">
              Timeout must be between 1 and {maxTimeout}.
            </span>
          )}
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={!formValid || busy}
          onClick={handleValidate}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Validate
        </button>
        <button
          type="button"
          disabled={!validated || !formValid || busy}
          onClick={handleDryRun}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Run Dry Run
        </button>
        <button
          type="button"
          disabled={!canRunLive || busy}
          onClick={handleRunLive}
          title={
            !validated
              ? 'Validate first'
              : !dryRunDone
                ? 'Dry run required first'
                : !confirmed
                  ? 'Confirm you understand this will contact external services'
                  : undefined
          }
          className={`rounded-md px-2.5 py-1 text-xs font-semibold text-white ${
            !canRunLive || busy ? 'cursor-not-allowed bg-slate-300' : 'bg-orange-600 hover:bg-orange-700'
          }`}
        >
          Run Live
        </button>
      </div>

      {!dryRunDone && <DryRunRequiredMessage />}

      {dryRunDone && <ConfirmLiveRunToggle checked={confirmed} onChange={setConfirmed} />}

      {validateError && (
        <p className="mt-2 text-xs font-medium text-red-600" data-testid="validate-error">
          {validateError}
        </p>
      )}

      {validateResult && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
          {validateMock && (
            <p className="mb-1 font-semibold text-amber-700" data-testid="validate-mock-indicator">
              Offline / Mock Data — validation simulated, no real backend call.
            </p>
          )}
          <p>
            <span className="font-semibold">argv preview:</span>{' '}
            {validateResult.argv_preview ? validateResult.argv_preview.join(' ') : 'n/a'}
          </p>
          <p>
            <span className="font-semibold">estimated input tokens:</span>{' '}
            {validateResult.estimated_input_tokens ?? 'n/a'}
          </p>
          <p>
            <span className="font-semibold">estimated output tokens:</span>{' '}
            {validateResult.estimated_output_tokens ?? 'n/a'}
          </p>
          {validateResult.warnings.length > 0 && (
            <p className="mt-1 text-amber-700">{validateResult.warnings.join(' · ')}</p>
          )}
        </div>
      )}

      {dryRunError && (
        <p className="mt-2 text-xs font-medium text-red-600" data-testid="dry-run-error">
          {dryRunError}
        </p>
      )}

      {dryRunResult && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
          <p className="font-semibold text-slate-600">Dry run result (run {dryRunResult.run_id}):</p>
          {dryRunResult.stdout && <pre className="whitespace-pre-wrap">{dryRunResult.stdout}</pre>}
          {dryRunResult.stderr && <pre className="whitespace-pre-wrap text-red-600">{dryRunResult.stderr}</pre>}
        </div>
      )}

      {liveRunError && (
        <p className="mt-2 text-xs font-medium text-red-600" data-testid="live-run-error">
          {liveRunError}
        </p>
      )}

      {liveRunResult && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-900 p-2 text-xs text-slate-100">
          {liveRunMock && (
            <p className="mb-1 font-semibold text-amber-300" data-testid="run-mock-indicator">
              Offline / Mock Data — run simulated, no real backend call.
            </p>
          )}
          {liveRunResult.stdout && <pre className="whitespace-pre-wrap">{liveRunResult.stdout}</pre>}
          {liveRunResult.stderr && <pre className="whitespace-pre-wrap text-red-300">{liveRunResult.stderr}</pre>}
        </div>
      )}
    </div>
  )
}
