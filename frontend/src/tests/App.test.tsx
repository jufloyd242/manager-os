import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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
})

describe('App', () => {
  it('renders the System Status heading', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'System Status' })).toBeInTheDocument()
  })

  it('renders at least one system status card', async () => {
    render(<App />)
    expect(await screen.findByText(mockSystemStatus[0].label)).toBeInTheDocument()
  })

  it('renders at least one recommended action title from mock data', async () => {
    render(<App />)
    expect(
      await screen.findByText(mockDailyOperatingLoop.recommended_actions[0].title),
    ).toBeInTheDocument()
  })

  it('shows backend-unavailable state when the API is unreachable', async () => {
    vi.mocked(getStatus).mockRejectedValueOnce(new Error('network error'))
    vi.mocked(getDaily).mockRejectedValueOnce(new Error('network error'))

    render(<App />)

    expect(await screen.findByText(/Backend is not available/i)).toBeInTheDocument()
  })
})


