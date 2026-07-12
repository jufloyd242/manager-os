import { useState, useEffect, useCallback, useRef } from 'react'
import {
  getMeetings,
  syncCalendar,
  getMeetingPrep,
  regeneratePrep,
} from '../api/client'
import type {
  MeetingEvent,
  MeetingPrepResponse,
  CalendarSyncResponse,
} from '../api/client'
import { MeetingPrepPanel } from './MeetingPrepPanel'

function formatDateLabel(d: Date): string {
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatDateShort(d: Date): string {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatTime(t: string): string {
  if (!t) return ''
  try {
    const parts = t.split('T')
    const timePart = parts.length > 1 ? parts[1] : t
    const [h, m] = timePart.split(':')
    if (!h) return t
    const hour = parseInt(h, 10)
    const ampm = hour >= 12 ? 'PM' : 'AM'
    const hour12 = hour % 12 || 12
    return `${hour12}:${m || '00'} ${ampm}`
  } catch {
    return t
  }
}

function formatSyncDate(d: Date): string {
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric' })
}

interface MeetingsViewProps {
  initialDate?: string
  initialMeetingId?: string
}

export function MeetingsView({ initialDate, initialMeetingId }: MeetingsViewProps) {
  const [selectedDate, setSelectedDate] = useState(() => {
    if (initialDate) return new Date(initialDate + 'T00:00:00')
    return new Date()
  })
  const [meetings, setMeetings] = useState<MeetingEvent[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [syncInfo, setSyncInfo] = useState<{ last_synced: string | null; source: string; stale: boolean } | null>(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<CalendarSyncResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedMeetingId, setSelectedMeetingId] = useState<string | null>(initialMeetingId || null)
  const [prep, setPrep] = useState<MeetingPrepResponse | null>(null)
  const [prepLoading, setPrepLoading] = useState(false)
  const [prepError, setPrepError] = useState<string | null>(null)
  const prevMeetingsRef = useRef<MeetingEvent[]>([])

  const dateStr = selectedDate.toISOString().split('T')[0]

  const loadMeetings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await getMeetings(dateStr)
      setMeetings(result.data.meetings || [])
      setWarnings(result.data.warnings || [])
      setSyncInfo(result.data.sync_info || null)
    } catch {
      setError('Failed to load meetings. Is the backend running?')
      setMeetings([])
    } finally {
      setLoading(false)
    }
  }, [dateStr])

  useEffect(() => {
    loadMeetings()
  }, [loadMeetings])

  // Preserve selected meeting when data reloads if it still exists
  useEffect(() => {
    if (selectedMeetingId && meetings.length > 0) {
      const stillExists = meetings.find(m => m.id === selectedMeetingId)
      if (!stillExists) {
        setSelectedMeetingId(null)
        setPrep(null)
      }
    }
    prevMeetingsRef.current = meetings
  }, [meetings, selectedMeetingId])

  // Load prep for initial meeting or selected meeting
  useEffect(() => {
    if (selectedMeetingId) {
      const meeting = meetings.find(m => m.id === selectedMeetingId)
      if (meeting) {
        setPrepLoading(true)
        setPrepError(null)
        getMeetingPrep(selectedMeetingId)
          .then(result => setPrep(result.data))
          .catch(() => setPrepError('Failed to load meeting preparation.'))
          .finally(() => setPrepLoading(false))
      }
    }
  }, [selectedMeetingId, meetings])

  const handleSync = async () => {
    setSyncing(true)
    setSyncResult(null)
    setError(null)
    try {
      const result = await syncCalendar(dateStr)
      setSyncResult(result.data)
      if (result.data.ok) {
        setMeetings(result.data.meetings || [])
        setSyncInfo({ last_synced: result.data.retrieved_at, source: result.data.source, stale: false })
      }
      if (result.data.errors?.length) {
        setError(result.data.errors.join('; '))
      }
    } catch {
      setError('Calendar sync failed. Is Gemini CLI configured?')
    } finally {
      setSyncing(false)
    }
  }

  const handlePrevDay = () => {
    const prev = new Date(selectedDate)
    prev.setDate(prev.getDate() - 1)
    setSelectedDate(prev)
  }

  const handleNextDay = () => {
    const next = new Date(selectedDate)
    next.setDate(next.getDate() + 1)
    setSelectedDate(next)
  }

  const handleSelectMeeting = (m: MeetingEvent) => {
    setSelectedMeetingId(m.id)
  }

  const handleRegeneratePrep = async () => {
    if (!selectedMeetingId) return
    setPrepLoading(true)
    setPrepError(null)
    // Preserve existing prep on regeneration failure
    const existingPrep = prep
    try {
      const result = await regeneratePrep(selectedMeetingId)
      setPrep(result.data)
    } catch {
      setPrepError('Failed to regenerate preparation. Previous prep retained.')
      // Keep existing prep usable
      if (!existingPrep) {
        setPrep(null)
      }
    } finally {
      setPrepLoading(false)
    }
  }

  const isToday = dateStr === new Date().toISOString().split('T')[0]
  const selectedMeeting = selectedMeetingId ? meetings.find(m => m.id === selectedMeetingId) : null

  // Date-specific sync language
  const syncLabel = syncInfo?.last_synced
    ? `Last synced: ${new Date(syncInfo.last_synced).toLocaleTimeString()}`
    : isToday
      ? 'Not synced today'
      : `Not synced for ${formatDateShort(selectedDate)}`

  return (
    <div className="flex flex-col h-full">
      {/* Fixed toolbar */}
      <div className="shrink-0 bg-white border-b border-slate-200 px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              onClick={handlePrevDay}
              className="p-2 rounded-lg hover:bg-slate-100 text-slate-500 hover:text-slate-800 transition-colors cursor-pointer"
              aria-label="Previous day"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>

            <div className="text-center min-w-[180px]">
              <input
                type="date"
                value={dateStr}
                onChange={(e) => {
                  if (e.target.value) {
                    setSelectedDate(new Date(e.target.value + 'T00:00:00'))
                    setSelectedMeetingId(null)
                    setPrep(null)
                  }
                }}
                className="text-base font-bold text-slate-900 bg-transparent border-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500 rounded px-2 py-1"
              />
              <p className="text-xs text-slate-500 mt-0.5">
                {isToday ? 'Today' : formatDateLabel(selectedDate)}
              </p>
            </div>

            <button
              onClick={handleNextDay}
              className="p-2 rounded-lg hover:bg-slate-100 text-slate-500 hover:text-slate-800 transition-colors cursor-pointer"
              aria-label="Next day"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>

          <div className="flex items-center gap-3">
            {syncInfo && (
              <span className="text-xs text-slate-400">{syncLabel}</span>
            )}
            <button
              onClick={handleSync}
              disabled={syncing}
              className={`px-3 py-1.5 rounded-lg font-medium text-sm text-white flex items-center gap-2 transition-all cursor-pointer ${
                syncing ? 'bg-slate-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700'
              }`}
            >
              {syncing ? (
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H17" />
                </svg>
              )}
              {syncing ? 'Syncing...' : `Sync ${formatSyncDate(selectedDate)}`}
            </button>
          </div>
        </div>

        {syncResult && !syncResult.ok && (
          <div className="mt-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
            Sync failed: {syncResult.errors?.join('; ') || 'Unknown error'}
          </div>
        )}
        {error && !syncResult && (
          <div className="mt-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
      </div>

      {/* Two-panel layout with internal scrolling */}
      <div className="flex-1 flex overflow-hidden">
        {/* Agenda panel */}
        <div className="w-1/2 overflow-y-auto border-r border-slate-200">
          <div className="px-4 py-2 sticky top-0 bg-white border-b border-slate-100 z-10">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
              Agenda — {formatDateShort(selectedDate)}
              <span className="ml-2 font-normal">
                ({meetings.length} event{meetings.length !== 1 ? 's' : ''})
              </span>
            </h3>
          </div>

          {loading ? (
            <div className="p-8 text-center text-sm text-slate-400">
              <div className="animate-pulse space-y-3">
                <div className="h-4 bg-slate-100 rounded w-3/4 mx-auto" />
                <div className="h-4 bg-slate-100 rounded w-1/2 mx-auto" />
              </div>
            </div>
          ) : meetings.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-400">
              <p className="font-medium text-slate-500">No events for this date.</p>
              <p className="text-xs mt-1">Sync calendar or choose another date.</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {meetings.map((m) => {
                const isSelected = selectedMeetingId === m.id
                return (
                  <button
                    key={m.id}
                    onClick={() => handleSelectMeeting(m)}
                    className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors cursor-pointer ${
                      isSelected ? 'bg-indigo-50 border-l-4 border-l-indigo-500' : ''
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          {m.start_time && (
                            <span className="text-xs font-semibold text-slate-600 shrink-0">
                              {formatTime(m.start_time)}
                              {m.end_time ? `–${formatTime(m.end_time)}` : ''}
                            </span>
                          )}
                          <h4 className="text-sm font-medium text-slate-800 truncate">{m.title}</h4>
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                          {m.attendees && m.attendees.length > 0 && (
                            <span className="truncate">
                              {m.attendees.slice(0, 3).join(', ')}
                              {m.attendees.length > 3 && ` +${m.attendees.length - 3}`}
                            </span>
                          )}
                        </div>
                        {m.location && (
                          <p className="text-xs text-slate-400 mt-0.5 truncate">{m.location}</p>
                        )}
                      </div>
                      {isSelected && (
                        <svg className="w-4 h-4 text-indigo-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}

          {warnings.length > 0 && (
            <div className="m-4 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-700">
              {warnings.map((w, i) => <p key={i}>{w}</p>)}
            </div>
          )}
        </div>

        {/* Prep panel */}
        <div className="w-1/2 overflow-y-auto">
          {!selectedMeeting ? (
            <div className="p-8 text-center text-sm text-slate-400 h-full flex flex-col items-center justify-center">
              <svg className="w-10 h-10 mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p className="font-medium text-slate-500">Select a meeting to view preparation.</p>
            </div>
          ) : prepLoading && !prep ? (
            <div className="p-8 text-center text-sm text-slate-400">
              <div className="animate-pulse space-y-3">
                <div className="h-5 bg-slate-100 rounded w-1/2 mx-auto" />
                <div className="h-4 bg-slate-100 rounded w-3/4 mx-auto" />
                <div className="h-4 bg-slate-100 rounded w-2/3 mx-auto" />
              </div>
            </div>
          ) : prepError && !prep ? (
            <div className="p-4 m-4 rounded-lg border border-red-200 bg-red-50 text-sm text-red-700">
              {prepError}
            </div>
          ) : prep ? (
            <MeetingPrepPanel
              prep={prep}
              onRegenerate={handleRegeneratePrep}
              regenerating={prepLoading}
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}
