import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DailySection } from '../components/DailySection'

// Mock the API client
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getStaffingBalance: vi.fn(),
  }
})

import { getStaffingBalance } from '../api/client'

const mockStaffingBalanceResponse = {
  comparison: [
    { person: 'Priya Nair', original_allocation: 1.28, balanced_allocation: 1.0 },
    { person: 'Jordan Lee', original_allocation: 0.8, balanced_allocation: 1.08 },
  ],
  redistributions: [
    { from_person: 'Priya Nair', to_person: 'Jordan Lee', amount: 0.28, project: 'Acme Corp — Phase 2' },
  ],
}

describe('StaffingBalanceUI', () => {
  beforeEach(() => {
    vi.mocked(getStaffingBalance).mockReset().mockResolvedValue({
      data: mockStaffingBalanceResponse,
      isMock: false,
    })
  })

  it('renders the Preview Rebalance button next to the overallocation alert badge', () => {
    const items = [
      { person: 'Jordan Lee', signal: 'No 1:1 in 18 days', severity: 'medium' },
      { person: 'Priya Nair', signal: 'Overallocated 128% next 2 weeks', severity: 'high' },
    ]

    render(<DailySection title="People / Staffing" items={items} />)

    // Check that we render the alert badge for Priya Nair
    const alertBadge = screen.getByText(/^Overallocated$/)
    expect(alertBadge).toBeInTheDocument()

    // Check that the Preview Rebalance button is rendered
    const button = screen.getByRole('button', { name: /Preview Rebalance/i })
    expect(button).toBeInTheDocument()
  })

  it('opens a high-fidelity modal/pane with original vs balanced allocation and planned redistributions when clicked', async () => {
    const items = [
      { person: 'Jordan Lee', signal: 'No 1:1 in 18 days', severity: 'medium' },
      { person: 'Priya Nair', signal: 'Overallocated 128% next 2 weeks', severity: 'high' },
    ]

    render(<DailySection title="People / Staffing" items={items} />)

    const button = screen.getByRole('button', { name: /Preview Rebalance/i })
    await userEvent.click(button)

    // Verify API call is made
    expect(getStaffingBalance).toHaveBeenCalledTimes(1)

    // Wait for modal/pane to appear and check the title
    expect(await screen.findByText(/Staffing Rebalance Preview/i)).toBeInTheDocument()

    // Check original vs balanced comparison is rendered using getAllByText to avoid collisions
    expect(screen.getAllByText(/Priya Nair/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/128%/).length).toBeGreaterThan(0) // Original
    expect(screen.getAllByText(/100%/).length).toBeGreaterThan(0) // Balanced

    expect(screen.getAllByText(/Jordan Lee/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/80%/).length).toBeGreaterThan(0) // Original
    expect(screen.getAllByText(/108%/).length).toBeGreaterThan(0) // Balanced

    // Check redistributions list is rendered (using a robust text matcher for nested nodes)
    expect(screen.getByText((_content, element) => {
      const hasText = (node: Element | null) =>
        node?.textContent?.includes('Jordan Lee') === true &&
        node?.textContent?.includes('from Priya Nair') === true;
      const childrenDontHaveText = Array.from(element?.children || []).every(child => !hasText(child as Element));
      return hasText(element) && childrenDontHaveText;
    })).toBeInTheDocument()
    expect(screen.getByText(/0\.28\s*FTE/i)).toBeInTheDocument() // amount
    expect(screen.getByText(/Acme Corp\s*—\s*Phase\s*2/i)).toBeInTheDocument() // project

    // Close the modal
    const closeButton = screen.getAllByRole('button', { name: /Close/i })[0]
    await userEvent.click(closeButton)

    // Check that modal/pane is removed
    await waitFor(() => {
      expect(screen.queryByText(/Staffing Rebalance Preview/i)).not.toBeInTheDocument()
    })
  })
})
