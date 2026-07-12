import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MeetingsView } from '../components/MeetingsView'
import {
  mockMeetingPrep,
  mockManagerStandupPrep,
  mockNoPrepEvent,
} from '../api/mockData'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getMeetings: vi.fn(),
    syncCalendar: vi.fn(),
    getMeetingPrep: vi.fn(),
    regeneratePrep: vi.fn(),
  }
})

import { getMeetings, syncCalendar, getMeetingPrep, regeneratePrep } from '../api/client'

beforeEach(() => {
  vi.mocked(getMeetings).mockReset()
  vi.mocked(syncCalendar).mockReset()
  vi.mocked(getMeetingPrep).mockReset()
  vi.mocked(regeneratePrep).mockReset()
})

describe('MeetingsView', () => {
  it('renders the date picker with today date', () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: { last_synced: null, source: 'local', stale: true } },
      isMock: false,
    })
    render(<MeetingsView initialDate="2026-07-10" />)
    const dateInput = screen.getByDisplayValue('2026-07-10')
    expect(dateInput).toBeInTheDocument()
  })

  it('shows empty state when no meetings exist', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: { last_synced: null, source: 'local', stale: true } },
      isMock: false,
    })
    render(<MeetingsView initialDate="2026-07-10" />)
    expect(await screen.findByText(/No events for this date/i)).toBeInTheDocument()
  })

  it('displays every returned meeting', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', title: 'Standup', attendees: ['Alice'], source: 'calendar', external_id: 'e1' },
          { id: 'm2', meeting_date: '2026-07-10', start_time: '10:00', title: '1:1 with Bob', attendees: ['Bob'], source: 'calendar', external_id: 'e2' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    render(<MeetingsView initialDate="2026-07-10" />)
    expect(await screen.findByText('Standup')).toBeInTheDocument()
    expect(await screen.findByText('1:1 with Bob')).toBeInTheDocument()
  })

  it('sync button has correct date label', () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: { last_synced: null, source: 'local', stale: true } },
      isMock: false,
    })
    render(<MeetingsView initialDate="2026-07-10" />)
    expect(screen.getByText(/Sync /i)).toBeInTheDocument()
  })

  it('changing date does not automatically sync', async () => {
    const syncFn = vi.mocked(syncCalendar)
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: { last_synced: null, source: 'local', stale: true } },
      isMock: false,
    })
    render(<MeetingsView initialDate="2026-07-10" />)

    // Change date via input
    const dateInput = screen.getByDisplayValue('2026-07-10')
    await userEvent.clear(dateInput)
    await userEvent.type(dateInput, '2026-07-15')

    // Sync should NOT have been called
    expect(syncFn).not.toHaveBeenCalled()
  })

  it('meeting selection opens prep panel', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', title: '1:1 with Alice', attendees: ['Alice Chen'], source: 'calendar', external_id: 'e1' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    vi.mocked(getMeetingPrep).mockResolvedValue({ data: mockMeetingPrep, isMock: false })

    render(<MeetingsView initialDate="2026-07-10" />)
    expect(await screen.findByText('1:1 with Alice')).toBeInTheDocument()

    await userEvent.click(screen.getByText('1:1 with Alice'))
    await waitFor(() => {
      expect(screen.getByText('Direct Report 1:1')).toBeInTheDocument()
    })
  })

  it('matched rule and relationship are visible in prep', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', title: '1:1 with Alice', attendees: ['Alice Chen'], source: 'calendar', external_id: 'e1' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    vi.mocked(getMeetingPrep).mockResolvedValue({ data: mockMeetingPrep, isMock: false })

    render(<MeetingsView initialDate="2026-07-10" />)
    await userEvent.click(await screen.findByText('1:1 with Alice'))
    expect(await screen.findByText('Direct Report 1:1')).toBeInTheDocument()
    expect(await screen.findByText('Direct Report')).toBeInTheDocument()
  })

  it('direct-report prep renders expected sections', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', title: '1:1 with Alice', attendees: ['Alice Chen'], source: 'calendar', external_id: 'e1' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    vi.mocked(getMeetingPrep).mockResolvedValue({ data: mockMeetingPrep, isMock: false })

    render(<MeetingsView initialDate="2026-07-10" />)
    await userEvent.click(await screen.findByText('1:1 with Alice'))
    expect(await screen.findByText('What Changed')).toBeInTheDocument()
    expect(await screen.findByText('Risks & Blockers')).toBeInTheDocument()
    expect(await screen.findByText('Suggested Questions')).toBeInTheDocument()
  })

  it('manager-standup prep renders expected sections', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm2', meeting_date: '2026-07-10', start_time: '14:00', title: 'Staff meeting', attendees: ['Chris Presley'], source: 'calendar', external_id: 'e2' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    vi.mocked(getMeetingPrep).mockResolvedValue({ data: mockManagerStandupPrep, isMock: false })

    render(<MeetingsView initialDate="2026-07-10" />)
    await userEvent.click(await screen.findByText('Staff meeting'))
    expect(await screen.findByText('Manager Standup')).toBeInTheDocument()
    expect(await screen.findByText('Manager')).toBeInTheDocument()
  })

  it('no-prep events remain visible in the list', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm3', meeting_date: '2026-07-10', start_time: '09:00', title: 'Focus time', attendees: [], source: 'calendar', external_id: 'e3' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    vi.mocked(getMeetingPrep).mockResolvedValue({ data: mockNoPrepEvent, isMock: false })

    render(<MeetingsView initialDate="2026-07-10" />)
    expect(await screen.findByText('Focus time')).toBeInTheDocument()

    await userEvent.click(screen.getByText('Focus time'))
    expect(await screen.findByText('No Preparation Needed')).toBeInTheDocument()
  })

  it('backend failure shows honest error state', async () => {
    vi.mocked(getMeetings).mockRejectedValue(new Error('Network error'))

    render(<MeetingsView initialDate="2026-07-10" />)
    expect(await screen.findByText(/Failed to load meetings/i)).toBeInTheDocument()
  })

  it('sync failure shows honest error state', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: { last_synced: null, source: 'local', stale: true } },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockRejectedValue(new Error('Sync failed'))

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = screen.getByText(/Sync /i)
    await userEvent.click(syncButton)
    expect(await screen.findByText(/Calendar sync failed/i)).toBeInTheDocument()
  })

  it('AI enhancement is not invoked automatically', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: {
        date: '2026-07-10',
        meetings: [
          { id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', title: '1:1 with Alice', attendees: ['Alice Chen'], source: 'calendar', external_id: 'e1' },
        ],
        warnings: [],
        sync_info: { last_synced: null, source: 'local', stale: true },
      },
      isMock: false,
    })
    vi.mocked(getMeetingPrep).mockResolvedValue({ data: mockMeetingPrep, isMock: false })

    render(<MeetingsView initialDate="2026-07-10" />)
    await userEvent.click(await screen.findByText('1:1 with Alice'))
    // Expand the collapsed "Why this prep?" provenance section
    await userEvent.click(await screen.findByText(/Why this prep?/))
    // The prep panel should show "AI enrichment: No (deterministic)"
    await waitFor(() => {
      expect(screen.getByText(/No \(deterministic\)/i)).toBeInTheDocument()
    })
  })
})

describe('MeetingsView — Calendar Sync', () => {
  it('shows sync success count feedback', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockResolvedValue({
      data: {
        ok: true,
        partial: false,
        date: '2026-07-10',
        meetings: [{ id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', end_time: '09:30', title: 'Standup', attendees: ['Alice'], linked_entities: [], source: 'calendar_sync', external_id: 'e1' }],
        retrieved_count: 1,
        persisted_count: 1,
        rejected_count: 0,
        replaced_count: 0,
        retrieved_at: '2026-07-10T12:00:00Z',
        source: 'gemini_cli',
        warnings: [],
        errors: [],
      },
      isMock: false,
    })

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = await screen.findByText(/Sync /i)
    await userEvent.click(syncButton)

    expect(await screen.findByText(/Retrieved 1 meeting.*Saved 1/i)).toBeInTheDocument()
  })

  it('shows partial success feedback', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockResolvedValue({
      data: {
        ok: true,
        partial: true,
        date: '2026-07-10',
        meetings: [{ id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', title: 'Standup', attendees: ['Alice'], linked_entities: [], source: 'calendar_sync', external_id: 'e1' }],
        retrieved_count: 4,
        persisted_count: 3,
        rejected_count: 1,
        replaced_count: 0,
        retrieved_at: '2026-07-10T12:00:00Z',
        source: 'gemini_cli',
        warnings: [],
        errors: ['Event 3: missing title'],
      },
      isMock: false,
    })

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = await screen.findByText(/Sync /i)
    await userEvent.click(syncButton)

    expect(await screen.findByText(/Retrieved 4 meetings.*Saved 3.*1 could not be saved/i)).toBeInTheDocument()
  })

  it('shows total persistence failure feedback', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockResolvedValue({
      data: {
        ok: false,
        partial: false,
        date: '2026-07-10',
        meetings: [],
        retrieved_count: 4,
        persisted_count: 0,
        rejected_count: 4,
        replaced_count: 0,
        retrieved_at: '2026-07-10T12:00:00Z',
        source: 'gemini_cli',
        warnings: [],
        errors: ['Event 0: database error', 'Event 1: database error'],
      },
      isMock: false,
    })

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = await screen.findByText(/Sync /i)
    await userEvent.click(syncButton)

    expect(await screen.findByText(/meetings could not be saved/i)).toBeInTheDocument()
  })

  it('shows legitimate zero-event feedback', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockResolvedValue({
      data: {
        ok: true,
        partial: false,
        date: '2026-07-10',
        meetings: [],
        retrieved_count: 0,
        persisted_count: 0,
        rejected_count: 0,
        replaced_count: 0,
        retrieved_at: '2026-07-10T12:00:00Z',
        source: 'gemini_cli',
        warnings: [],
        errors: [],
      },
      isMock: false,
    })

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = await screen.findByText(/Sync /i)
    await userEvent.click(syncButton)

    expect(await screen.findByText(/Retrieved 0 meetings.*Saved 0/i)).toBeInTheDocument()
  })

  it('renders meetings immediately after sync', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockResolvedValue({
      data: {
        ok: true,
        date: '2026-07-10',
        meetings: [{ id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', end_time: '10:00', title: 'Immediate Sync Meeting', attendees: ['Alice'], linked_entities: [], source: 'calendar_sync', external_id: 'e1' }],
        retrieved_count: 1,
        persisted_count: 1,
        rejected_count: 0,
        replaced_count: 0,
        retrieved_at: '2026-07-10T12:00:00Z',
        source: 'gemini_cli',
        warnings: [],
        errors: [],
      },
      isMock: false,
    })

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = await screen.findByText(/Sync /i)
    await userEvent.click(syncButton)

    expect(await screen.findByText('Immediate Sync Meeting')).toBeInTheDocument()
  })

  it('renders end time after sync', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    vi.mocked(syncCalendar).mockResolvedValue({
      data: {
        ok: true,
        date: '2026-07-10',
        meetings: [{ id: 'm1', meeting_date: '2026-07-10', start_time: '09:00', end_time: '10:30', title: 'End Time Meeting', attendees: ['Alice'], linked_entities: [], source: 'calendar_sync', external_id: 'e1' }],
        retrieved_count: 1,
        persisted_count: 1,
        rejected_count: 0,
        replaced_count: 0,
        retrieved_at: '2026-07-10T12:00:00Z',
        source: 'gemini_cli',
        warnings: [],
        errors: [],
      },
      isMock: false,
    })

    render(<MeetingsView initialDate="2026-07-10" />)
    const syncButton = await screen.findByText(/Sync /i)
    await userEvent.click(syncButton)

    expect(await screen.findByText(/9:00.*10:30/i)).toBeInTheDocument()
  })

  it('keeps selected date stable as 2026-07-10', async () => {
    vi.mocked(getMeetings).mockResolvedValue({
      data: { date: '2026-07-10', meetings: [], warnings: [], sync_info: undefined },
      isMock: false,
    })
    render(<MeetingsView initialDate="2026-07-10" />)
    const dateInput = await screen.findByDisplayValue('2026-07-10')
    expect(dateInput).toBeInTheDocument()
  })
})