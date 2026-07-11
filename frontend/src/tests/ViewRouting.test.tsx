import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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
  it('defaults to daily_loop view and switches between daily_loop, deals, and forecast views via sidebar/navigation', async () => {
    render(<App />)

    // Verify initially in daily_loop view — shows Top Actions and Command Center
    expect(await screen.findByRole('heading', { name: 'Top Actions' })).toBeInTheDocument()
    expect(screen.getByText('Command Center')).toBeInTheDocument()

    // Find sidebar navigation buttons — use getAllByRole and pick the sidebar one
    const sidebarButtons = screen.getAllByRole('button')
    const dealsBtn = sidebarButtons.find(b => b.textContent?.trim() === 'Deals')!
    const forecastBtn = sidebarButtons.find(b => b.textContent?.trim() === 'Forecast')!
    const todayBtn = sidebarButtons.find(b => b.textContent?.trim() === 'Today')!

    // Click on Deals view
    await userEvent.click(dealsBtn)

    // Verify active view switched to deals
    expect(screen.queryByText('Command Center')).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Deals' })).toBeInTheDocument()

    // Click on Forecast view
    await userEvent.click(forecastBtn)

    // Verify active view switched to forecast
    expect(await screen.findByRole('heading', { name: 'Forecast' })).toBeInTheDocument()

    // Click back to Today view
    await userEvent.click(todayBtn)
    expect(await screen.findByRole('heading', { name: 'Top Actions' })).toBeInTheDocument()
    expect(screen.getByText('Command Center')).toBeInTheDocument()
  })

  it('verifies that the sidebar navigation renders correctly', async () => {
    render(<App />)

    // Wait for the asynchronous data to load
    await screen.findByRole('heading', { name: 'Top Actions' })

    // Verify sidebar has the expected navigation items
    const sidebarButtons = screen.getAllByRole('button')
    expect(sidebarButtons.some(b => b.textContent?.trim() === 'Today')).toBe(true)
    expect(sidebarButtons.some(b => b.textContent?.trim() === 'Deals')).toBe(true)
    expect(sidebarButtons.some(b => b.textContent?.trim() === 'Forecast')).toBe(true)
    expect(sidebarButtons.some(b => b.textContent?.trim() === 'Meetings')).toBe(true)
  })
})
