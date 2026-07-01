import type { RiskLevel } from '../api/client'

const RISK_STYLES: Record<RiskLevel, string> = {
  local_safe: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  local_write: 'bg-blue-100 text-blue-800 border-blue-300',
  external_bounded: 'bg-amber-100 text-amber-800 border-amber-300',
  external_high_risk: 'bg-orange-100 text-orange-800 border-orange-400',
  blocked: 'bg-gray-200 text-red-700 border-gray-400 opacity-70 cursor-not-allowed',
}

const RISK_LABELS: Record<RiskLevel, string> = {
  local_safe: 'Local Safe',
  local_write: 'Local Write',
  external_bounded: 'External Bounded',
  external_high_risk: 'External High Risk',
  blocked: 'Blocked',
}

export function RiskBadge({ level }: { level: RiskLevel }) {
  return (
    <span
      data-testid={`risk-badge-${level}`}
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${RISK_STYLES[level]}`}
    >
      {RISK_LABELS[level]}
    </span>
  )
}
