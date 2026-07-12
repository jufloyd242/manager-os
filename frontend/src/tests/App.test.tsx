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
  localStorage.clear()
  window.location.hash = ''
})

describe('App', () => {
  it('renders Today page by default', async () => {
    render(<App />)
    expect(await screen.findByText(/Here's what needs your attention/i)).toBeInTheDocument()
  })

  it('renders sidebar with all groups', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)
    expect(screen.getByText('Work')).toBeInTheDocument()
    expect(screen.getByText('Context')).toBeInTheDocument()
    expect(screen.getByText('Operations')).toBeInTheDocument()
  })

  it('does not render Command Center on Today', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)
    expect(screen.queryByText('Command Center')).not.toBeInTheDocument()
  })

  it('does not render System Status heading on Today', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)
    expect(screen.queryByText('System Status')).not.toBeInTheDocument()
  })

  it('does not render Token Budget on Today', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)
    expect(screen.queryByText('Token Budget')).not.toBeInTheDocument()
  })

  it('does not render Run History on Today', async () => {
    render(<App />)
    await screen.findByText(/Here's what needs your attention/i)
    expect(screen.queryByText('Run History')).not.toBeInTheDocument()
  })

  it('shows error state when API is unreachable', async () => {
    vi.mocked(getStatus).mockRejectedValueOnce(new Error('network error'))
    vi.mocked(getDaily).mockRejectedValueOnce(new Error('network error'))

    render(<App />)
    expect(await screen.findByText(/Failed to load daily data/i)).toBeInTheDocument()
  })
})
