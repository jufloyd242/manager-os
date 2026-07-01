import type { TokenBudget } from '../api/client'

export function TokenBudgetPanel({ budget }: { budget: TokenBudget }) {
  const pendingTotal = budget.pending.reduce((sum, entry) => sum + entry.estimated_input_tokens, 0)
  const projected = budget.used_tokens + pendingTotal
  const pct = Math.min(100, Math.round((projected / budget.daily_budget_tokens) * 100))
  const barColor = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500'

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-700">Token / Cost Guardrail</h3>
      <div className="mt-3">
        <div className="flex justify-between text-xs text-slate-500">
          <span>
            {projected.toLocaleString()} / {budget.daily_budget_tokens.toLocaleString()} tokens
          </span>
          <span>{pct}%</span>
        </div>
        <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
          <div className={`h-full ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
      </div>
      {budget.pending.length > 0 && (
        <ul className="mt-3 space-y-1">
          {budget.pending.map((entry) => (
            <li key={entry.command_id} className="flex justify-between text-xs text-slate-500">
              <span>{entry.label}</span>
              <span>{entry.estimated_input_tokens.toLocaleString()} tok</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
