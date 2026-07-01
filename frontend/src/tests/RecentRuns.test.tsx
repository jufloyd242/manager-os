import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RecentRuns } from '../components/RecentRuns'
import { mockRecentRuns } from '../api/mockData'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getRuns: vi.fn(),
    getRunLogs: vi.fn(),
  }
})

import { getRuns } from '../api/client'

beforeEach(() => {
  vi.mocked(getRuns).mockReset().mockResolvedValue({ data: mockRecentRuns, isMock: false })
})

describe('RecentRuns', () => {
  it('fetches and renders run history rows (command_id and status visible)', async () => {
    render(<RecentRuns />)
    const firstRun = mockRecentRuns[0]

    const commandCell = await screen.findByText(firstRun.command_id)
    const row = commandCell.closest('tr')

    expect(row).not.toBeNull()
    expect(row).toHaveTextContent(firstRun.status)
  })

  it('falls back to mock runs and shows an offline indicator when the API is unreachable', async () => {
    vi.mocked(getRuns).mockRejectedValueOnce(new Error('network error'))

    render(<RecentRuns />)

    expect(await screen.findByTestId('recent-runs-mock-indicator')).toBeInTheDocument()
  })
})
