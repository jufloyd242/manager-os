import type { TokenEstimate } from '../api/client'

export function TokenBudgetPanel({ estimate }: { estimate: TokenEstimate | null }) {
  if (!estimate) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-700">Token / Cost Guardrail</h3>
        <p className="mt-2 text-xs text-slate-500">
          No command validated or run yet. Validate a command to see its estimated token cost.
        </p>
      </div>
    )
  }

  const noTokenRisk = estimate.risk_level === 'local_safe' && !estimate.estimated_input_tokens

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-700">Token / Cost Guardrail</h3>
      <p className="mt-2 text-xs text-slate-500">{estimate.label}</p>
      {noTokenRisk ? (
        <p className="mt-1 text-sm font-semibold text-emerald-600">No token risk — local only</p>
      ) : (
        <p className="mt-1 text-lg font-bold text-slate-900">
          {estimate.estimated_input_tokens != null ? estimate.estimated_input_tokens.toLocaleString() : 'unknown'}
          <span className="ml-1 text-xs font-normal text-slate-500">estimated input tokens</span>
        </p>
      )}
    </div>
  )
}

