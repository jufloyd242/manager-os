// Small, shared UI pieces for the "guarded external call" pattern used by
// any command flow that requires a successful dry run + explicit
// confirmation before running live (currently `LiveSingleFetchFlow` and
// `DailyActionButtons`). Extracted so both consumers render identical
// markup/testids instead of duplicating this JSX.

export function ExternalCallWarning({ text }: { text?: string }) {
  return (
    <p
      className="mt-2 rounded-md border border-orange-300 bg-orange-50 px-2 py-1.5 text-xs font-medium text-orange-800"
      data-testid="external-call-warning"
    >
      {text ??
        'This will contact Google Drive via Gemini CLI. A successful dry run and explicit confirmation are required before running live.'}
    </p>
  )
}

export function DryRunRequiredMessage({ text }: { text?: string }) {
  return (
    <p className="mt-2 text-xs font-medium text-amber-700" data-testid="dry-run-required-message">
      {text ?? 'Dry run required first — run a dry run before confirming a live run.'}
    </p>
  )
}

export function ConfirmLiveRunToggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
}) {
  return (
    <label className="mt-3 flex items-center gap-2 text-xs text-slate-700" data-testid="confirm-live-run-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label ?? 'I understand this will contact external services (Google Drive via Gemini CLI).'}
    </label>
  )
}
