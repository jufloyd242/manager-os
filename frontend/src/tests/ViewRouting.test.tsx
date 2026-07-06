import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
    getStaffingBalance: vi.fn(),
  }
})

import { getStatus, getDaily, getCommands, getRuns } from '../api/client'

beforeEach(() => {
  vi.mocked(getStatus).mockReset().mockResolvedValue({ data: mockSystemStatus, isMock: false })
  vi.mocked(getDaily).mockReset().mockResolvedValue({ data: mockDailyOperatingLoop, isMock: false })
  vi.mocked(getCommands).mockReset().mockResolvedValue({ data: mockCommandRegistry, isMock: false })
  vi.mocked(getRuns).mockReset().mockResolvedValue({ data: mockRecentRuns, isMock: false })
})

describe('View Routing', () => {
  it('defaults to daily_loop view and switches between daily_loop, staffing, and archive views via sidebar/navigation', async () => {
    render(<App />)

    // Verify initially in daily_loop view
    // It should render the Action Inbox, and operational components (Command Center / Recent Runs)
    expect(await screen.findByRole('heading', { name: 'Action Inbox', level: 2 })).toBeInTheDocument()
    expect(screen.getByText('Command Center')).toBeInTheDocument()

    // Find sidebar navigation links or buttons
    const staffingNav = screen.getByRole('button', { name: /People \/ Staffing/i })
    const archiveNav = screen.getByRole('button', { name: /Archive/i })
    const dailyLoopNav = screen.getByRole('button', { name: /Daily Operating Loop/i })

    // Click on People/Staffing view
    await userEvent.click(staffingNav)

    // Verify active view switched to staffing
    // Staffing view should render the DailySection for "People / Staffing" and capacity balancing metrics/preview rebalance button
    expect(screen.queryByText('Action Inbox')).not.toBeInTheDocument()
    expect(screen.queryByText('Command Center')).not.toBeInTheDocument()
    
    // Use findByRole for h3 to avoid matching sidebar button text
    expect(await screen.findByRole('heading', { name: 'People / Staffing', level: 3 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Preview Rebalance/i })).toBeInTheDocument()

    // Click on Archive view
    await userEvent.click(archiveNav)

    // Verify active view switched to archive
    // Dedicated layout for historical data (e.g. Project Archive and LEGACY_EMPTY views)
    expect(screen.queryByText('Action Inbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'People / Staffing', level: 3 })).not.toBeInTheDocument()
    
    expect(await screen.findByRole('heading', { name: 'Project Archive', level: 3 })).toBeInTheDocument()
    expect((await screen.findAllByText('LEGACY_EMPTY')).length).toBeGreaterThan(0)

    // Click back to Daily Operating Loop view
    await userEvent.click(dailyLoopNav)
    expect(await screen.findByRole('heading', { name: 'Action Inbox', level: 2 })).toBeInTheDocument()
    expect(screen.getByText('Command Center')).toBeInTheDocument()
  })

  it('verifies that real-time counters/alert badges are fully synced and reactive across view changes', async () => {
    render(<App />)

    // Wait for the asynchronous data to load first
    await screen.findByRole('heading', { name: 'Action Inbox', level: 2 })

    // Check counters or badge in sidebar navigation
    const actionCountBadge = screen.getByTestId('nav-badge-daily_loop')
    expect(actionCountBadge).toHaveTextContent('5') // 5 recommended actions

    // Switch to Staffing view, and then verify the counters still exist in the sidebar and are correct.
    const staffingNav = screen.getByRole('button', { name: /People \/ Staffing/i })
    await userEvent.click(staffingNav)

    const actionCountBadgeAfter = screen.getByTestId('nav-badge-daily_loop')
    expect(actionCountBadgeAfter).toHaveTextContent('5')
  })
})
