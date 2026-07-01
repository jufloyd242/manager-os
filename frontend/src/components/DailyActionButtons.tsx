import { useState } from 'react'
import type { RecommendedAction, RunRecord, RunResponse } from '../api/client'
import { runCommand } from '../api/client'
import { ConfirmLiveRunToggle, DryRunRequiredMessage, ExternalCallWarning } from './GuardedExternalCallNotice'
import { RiskBadge } from './RiskBadge'

// Defense-in-depth: the backend must never send a batch-live command_id in a
// recommended action's primary/secondary commands, but if one somehow shows
// up here, render it as a disabled button with a reason rather than making
// it clickable. The real gate is the backend never sending it.
function isDisallowedCommandId(commandId: string): boolean {
  return /batch/i.test(commandId) && /live/i.test(commandId)
}

function toRunRecord(result: RunResponse): RunRecord {
  return {
    run_id: result.run_id,
    command_id: result.command_id,
    status: (result.status as RunRecord['status']) ?? 'success',
    dry_run: false,
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    stdout: result.stdout,
    stderr: result.stderr,
  }
}

function CommandOutput({ result, label }: { result: RunResponse | null; label: string }) {
  if (!result) return null
  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
      <p className="font-semibold text-slate-600">
        {label} (run {result.run_id}):
      </p>
      {result.stdout && <pre className="whitespace-pre-wrap">{result.stdout}</pre>}
      {result.stderr && <pre className="whitespace-pre-wrap text-red-600">{result.stderr}</pre>}
    </div>
  )
}

export interface DailyActionButtonsProps {
  action: RecommendedAction
  onRunRecorded?: (run: RunRecord) => void
}

/**
 * Renders "Dry Run Fetch" / "Print Prompt" / "Run Live Fetch" buttons for a
 * document-gap-sourced recommended action (one with `primary_command`
 * present), wired to real command_center commands with prefilled params.
 * Reuses the same "no live until dry-run success" + confirmation + warning
 * pattern as `LiveSingleFetchFlow` via the shared `GuardedExternalCallNotice`
 * pieces, but without the editable-parameter/Validate step — params come
 * prefilled from the action itself.
 */
export function DailyActionButtons({ action, onRunRecorded }: DailyActionButtonsProps) {
  const primary = action.primary_command
  const printPromptCmd = action.secondary_commands?.find(
    (c) => c.label === 'Print Prompt' || c.command_id === 'project_docs_fetch_print_prompt',
  )
  const liveCmd = action.secondary_commands?.find((c) => c.command_id === 'project_docs_fetch_live_single')

  const [dryRunResult, setDryRunResult] = useState<RunResponse | null>(null)
  const [dryRunRunId, setDryRunRunId] = useState<string | null>(null)
  const [dryRunError, setDryRunError] = useState<string | null>(null)
  const [dryRunBusy, setDryRunBusy] = useState(false)

  const [printPromptResult, setPrintPromptResult] = useState<RunResponse | null>(null)
  const [printPromptError, setPrintPromptError] = useState<string | null>(null)
  const [printPromptBusy, setPrintPromptBusy] = useState(false)

  const [confirmed, setConfirmed] = useState(false)
  const [liveResult, setLiveResult] = useState<RunResponse | null>(null)
  const [liveError, setLiveError] = useState<string | null>(null)
  const [liveBusy, setLiveBusy] = useState(false)

  if (!primary) return null

  const dryRunDone = dryRunRunId != null
  const canRunLive = dryRunDone && confirmed

  async function handleDryRun() {
    if (dryRunBusy) return
    setDryRunBusy(true)
    setDryRunError(null)
    try {
      const result = await runCommand(primary!.command_id, primary!.params, false)
      setDryRunResult(result.data)
      setDryRunRunId(result.data.run_id)
      onRunRecorded?.(toRunRecord(result.data))
    } catch {
      setDryRunResult(null)
      setDryRunRunId(null)
      setDryRunError('Dry run failed — could not reach the Manager OS API.')
    } finally {
      setDryRunBusy(false)
    }
  }

  async function handlePrintPrompt() {
    if (!printPromptCmd || printPromptBusy) return
    setPrintPromptBusy(true)
    setPrintPromptError(null)
    try {
      const result = await runCommand(printPromptCmd.command_id, printPromptCmd.params, false)
      setPrintPromptResult(result.data)
      onRunRecorded?.(toRunRecord(result.data))
    } catch {
      setPrintPromptResult(null)
      setPrintPromptError('Print prompt failed — could not reach the Manager OS API.')
    } finally {
      setPrintPromptBusy(false)
    }
  }

  async function handleRunLive() {
    if (!liveCmd || !canRunLive || liveBusy || dryRunRunId == null) return
    setLiveBusy(true)
    setLiveError(null)
    try {
      const params = {
        ...liveCmd.params,
        ...(liveCmd.requires_successful_dry_run ? { dry_run_run_id: dryRunRunId } : {}),
      }
      const result = await runCommand(liveCmd.command_id, params, true)
      setLiveResult(result.data)
      onRunRecorded?.(toRunRecord(result.data))
    } catch {
      setLiveResult(null)
      setLiveError('Live run failed — could not reach the Manager OS API.')
    } finally {
      setLiveBusy(false)
    }
  }

  const liveBlocked = liveCmd != null && isDisallowedCommandId(liveCmd.command_id)

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={dryRunBusy}
          onClick={handleDryRun}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Dry Run Fetch
        </button>

        {printPromptCmd && (
          <button
            type="button"
            disabled={printPromptBusy}
            onClick={handlePrintPrompt}
            className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Print Prompt
          </button>
        )}

        {liveCmd && liveBlocked && (
          <button
            type="button"
            disabled
            title="Batch/live commands of this kind are not permitted from this view."
            className="cursor-not-allowed rounded-md bg-slate-300 px-2.5 py-1 text-xs font-semibold text-white"
          >
            Run Live Fetch
          </button>
        )}

        {liveCmd && !liveBlocked && (
          <button
            type="button"
            disabled={!canRunLive || liveBusy}
            onClick={handleRunLive}
            title={
              !dryRunDone
                ? 'Dry run required first'
                : !confirmed
                  ? 'Confirm you understand this will contact external services'
                  : undefined
            }
            className={`rounded-md px-2.5 py-1 text-xs font-semibold text-white ${
              !canRunLive || liveBusy ? 'cursor-not-allowed bg-slate-300' : 'bg-orange-600 hover:bg-orange-700'
            }`}
          >
            Run Live Fetch
          </button>
        )}
      </div>

      {liveCmd && !liveBlocked && (
        <>
          <ExternalCallWarning />
          <div className="mt-1">
            <RiskBadge level="external_bounded" />
          </div>
          {!dryRunDone && <DryRunRequiredMessage text="Dry run required first — run Dry Run Fetch before confirming a live run." />}
          {dryRunDone && <ConfirmLiveRunToggle checked={confirmed} onChange={setConfirmed} />}
        </>
      )}

      {dryRunError && (
        <p className="mt-2 text-xs font-medium text-red-600" data-testid="dry-run-fetch-error">
          {dryRunError}
        </p>
      )}
      <CommandOutput result={dryRunResult} label="Dry run" />

      {printPromptError && (
        <p className="mt-2 text-xs font-medium text-red-600" data-testid="print-prompt-error">
          {printPromptError}
        </p>
      )}
      <CommandOutput result={printPromptResult} label="Prompt" />

      {liveError && (
        <p className="mt-2 text-xs font-medium text-red-600" data-testid="live-fetch-error">
          {liveError}
        </p>
      )}
      <CommandOutput result={liveResult} label="Live run" />
    </div>
  )
}
