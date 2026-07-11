import { useState } from 'react'
import type { MeetingPrepResponse } from '../api/client'

interface MeetingPrepPanelProps {
  prep: MeetingPrepResponse
  onRegenerate: () => void
}

function relationshipBadgeColor(rel: string | null): string {
  switch (rel) {
    case 'direct_report':
      return 'bg-blue-50 text-blue-700 border-blue-200'
    case 'manager':
      return 'bg-purple-50 text-purple-700 border-purple-200'
    case 'peer':
      return 'bg-green-50 text-green-700 border-green-200'
    case 'client':
      return 'bg-amber-50 text-amber-700 border-amber-200'
    case 'external':
      return 'bg-slate-50 text-slate-600 border-slate-200'
    default:
      return 'bg-slate-50 text-slate-500 border-slate-200'
  }
}

function relationshipLabel(rel: string): string {
  switch (rel) {
    case 'direct_report':
      return 'Direct Report'
    case 'manager':
      return 'Manager'
    case 'peer':
      return 'Peer'
    case 'client':
      return 'Client'
    case 'external':
      return 'External'
    default:
      return 'Unknown'
  }
}

function sectionIcon(sectionName: string): string {
  switch (sectionName) {
    case 'changes': return '🔄'
    case 'risks': case 'blockers': return '⚠️'
    case 'decisions': case 'decisions_needed': return '⚖️'
    case 'wins': return '🏆'
    case 'asks': return '🙋'
    case 'talking_points': return '💬'
    case 'questions': return '❓'
    case 'actions': case 'commitments': return '📋'
    case 'milestones': return '🎯'
    case 'prior_notes': return '📝'
    case 'deals_context': return '💼'
    case 'dependencies': return '🔗'
    case 'announcements': return '📢'
    default: return '•'
  }
}

function sectionTitle(sectionName: string): string {
  switch (sectionName) {
    case 'changes': return 'What Changed'
    case 'risks': case 'blockers': return 'Risks & Blockers'
    case 'decisions': return 'Open Decisions'
    case 'decisions_needed': return 'Decisions Needed'
    case 'wins': return 'Wins'
    case 'asks': return 'Asks'
    case 'talking_points': return 'Talking Points'
    case 'questions': return 'Suggested Questions'
    case 'actions': return 'Open Actions'
    case 'commitments': return 'Commitments'
    case 'milestones': return 'Milestones'
    case 'prior_notes': return 'Prior Notes'
    case 'deals_context': return 'Deal Context'
    case 'dependencies': return 'Dependencies'
    case 'announcements': return 'Announcements'
    default: return sectionName
  }
}

export function MeetingPrepPanel({ prep, onRegenerate }: MeetingPrepPanelProps) {
  const [showProvenance, setShowProvenance] = useState(false)

  const sectionNames = Object.keys(prep.sections)

  return (
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="border-b border-slate-100 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-base font-bold text-slate-900 truncate">{prep.meeting_title}</h3>
            <p className="text-xs text-slate-500 mt-1">
              {prep.meeting_date}
              {prep.meeting_time ? ` at ${prep.meeting_time}` : ''}
            </p>
          </div>
          <button
            onClick={onRegenerate}
            className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-bold text-indigo-600 hover:bg-indigo-50 border border-indigo-200 transition-colors cursor-pointer"
          >
            Regenerate
          </button>
        </div>

        {/* Attendees with relationship badges */}
        {prep.resolved_attendees.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {prep.resolved_attendees.map((ra, i) => (
              <span
                key={i}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${relationshipBadgeColor(ra.relationship)}`}
              >
                <span>{ra.person_name}</span>
                <span className="opacity-70">·</span>
                <span>{relationshipLabel(ra.relationship ?? '')}</span>
              </span>
            ))}
          </div>
        )}

        {/* Rule match */}
        <div className="mt-3 flex items-center gap-2">
          <span className="text-xs font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-md border border-indigo-100">
            {prep.matched_rule_name}
          </span>
          {!prep.prep_required && (
            <span className="text-xs font-bold text-slate-400 bg-slate-100 px-2 py-0.5 rounded-md">
              No prep needed
            </span>
          )}
        </div>
      </div>

      {/* Missing context warnings */}
      {prep.missing_context_warnings.length > 0 && (
        <div className="mx-5 mt-4 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-700">
          {prep.missing_context_warnings.map((w, i) => <p key={i}>{w}</p>)}
        </div>
      )}

      {/* Sections */}
      {prep.prep_required && sectionNames.length > 0 && (
        <div className="p-5 space-y-5">
          {sectionNames.map((sectionName) => {
            const items = prep.sections[sectionName] as Array<Record<string, unknown>> | undefined
            if (!items || items.length === 0) return null

            return (
              <div key={sectionName}>
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2 flex items-center gap-1.5">
                  <span>{sectionIcon(sectionName)}</span>
                  {sectionTitle(sectionName)}
                </h4>
                <div className="space-y-1.5">
                  {items.map((item, i) => {
                    const value = Object.values(item)[0] as string
                    return (
                      <p key={i} className="text-sm text-slate-700 leading-relaxed">
                        {value}
                      </p>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* AI enrichment indicator */}
      {prep.llm_enriched && (
        <div className="mx-5 mb-4 rounded-lg bg-indigo-50 border border-indigo-200 px-3 py-2 text-xs text-indigo-700">
          This preparation was enhanced with AI.
        </div>
      )}

      {/* Provenance section (collapsible) */}
      <div className="border-t border-slate-100">
        <button
          onClick={() => setShowProvenance(!showProvenance)}
          className="w-full px-5 py-3 flex items-center justify-between text-xs font-bold text-slate-500 hover:text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
        >
          <span>Why this prep?</span>
          <svg
            className={`w-4 h-4 transition-transform ${showProvenance ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {showProvenance && (
          <div className="px-5 pb-4 space-y-3 text-xs text-slate-500">
            <div>
              <p className="font-bold text-slate-600 mb-1">Matched Rule</p>
              <p className="text-slate-500">{prep.matched_rule_name} ({prep.matched_rule_id})</p>
              <p className="text-slate-400 mt-0.5">{prep.why_this_rule_matched}</p>
            </div>

            {prep.sources_selected.length > 0 && (
              <div>
                <p className="font-bold text-slate-600 mb-1">Sources Selected ({prep.sources_selected.length})</p>
                <div className="space-y-1">
                  {prep.sources_selected.map((s, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-slate-500">{s}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {prep.sources_consulted.length > 0 && (
              <div>
                <p className="font-bold text-slate-600 mb-1">Sources Consulted ({prep.sources_consulted.length})</p>
                <div className="space-y-1">
                  {prep.sources_consulted.map((s, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-slate-500">{s}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <p className="font-bold text-slate-600 mb-1">Generated</p>
              <p className="text-slate-400">{prep.generated_at}</p>
              <p className="text-slate-400">AI enrichment: {prep.llm_enriched ? 'Yes' : 'No (deterministic)'}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}