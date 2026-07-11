import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
import {
  mockDailyOperatingLoop,
  mockSystemStatus,
  mockCommandRegistry,
  mockRecentRuns,
} from '../api/mockData'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getStatus: vi.fn(),
    getDaily: vi.fn(),
    getCommands: vi.fn(),
    getRuns: vi.fn(),
    validateCommand: vi.fn(),
    runCommand: vi.fn(),
    getRunLogs: vi.fn(),
  }
})

import { getStatus, getDaily, getCommands, getRuns } from '../api/client'

beforeEach(() => {
  vi.mocked(getStatus).mockReset().mockResolvedValue({ data: mockSystemStatus, isMock: false })
  vi.mocked(getDaily).mockReset().mockResolvedValue({ data: mockDailyOperatingLoop, isMock: false })
  vi.mocked(getCommands).mockReset().mockResolvedValue({ data: mockCommandRegistry, isMock: false })
  vi.mocked(getRuns).mockReset().mockResolvedValue({ data: mockRecentRuns, isMock: false })
  localStorage.clear()
  window.location.hash = ''
})

describe('View Routing', () => {
  it('defaults to Today page', async () => {
    render(<App />)
    expect(await screen.findByText(/Here's what needs your attention/i)).toBeInTheDocument()
  })

  it('navigates to Deals when sidebar item is clicked', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)

    const dealsButtons = screen.getAllByText('Deals')
    fireEvent.click(dealsButtons[0])

    await waitFor(() => {
      expect(window.location.hash).toContain('deals')
    })
  })

  it('navigates to Meetings when sidebar item is clicked', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)

    const meetingsButtons = screen.getAllByText('Meetings')
    fireEvent.click(meetingsButtons[0])

    await waitFor(() => {
      expect(window.location.hash).toContain('meetings')
    })
  })

  it('navigates to Forecast when sidebar item is clicked', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)

    const forecastButtons = screen.getAllByText('Forecast')
    fireEvent.click(forecastButtons[0])

    await waitFor(() => {
      expect(window.location.hash).toContain('forecast')
    })
  })

  it('preserves route on hash change', async () => {
    window.location.hash = '/deals'
    render(<App />)

    await waitFor(() => {
      expect(window.location.hash).toContain('deals')
    })
  })
})
