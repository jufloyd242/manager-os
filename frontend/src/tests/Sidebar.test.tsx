import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Sidebar } from '../components/Sidebar'

describe('Sidebar Component', () => {
  it('renders with three views: Daily Operating Loop, People / Staffing, and Archive', () => {
    const handleViewChange = vi.fn()
    render(<Sidebar currentView="daily_loop" onViewChange={handleViewChange} />)
    
    expect(screen.getByText('Daily Operating Loop')).toBeInTheDocument()
    expect(screen.getByText('People / Staffing')).toBeInTheDocument()
    expect(screen.getByText('Archive')).toBeInTheDocument()
  })

  it('supports collapsible state and toggles correctly', () => {
    const handleViewChange = vi.fn()
    const { container } = render(
      <Sidebar currentView="daily_loop" onViewChange={handleViewChange} />
    )

    // Initially the sidebar is open/expanded
    const sidebarEl = container.firstChild as HTMLElement
    expect(sidebarEl).toHaveClass('w-64')

    // Find collapse toggle button
    const toggleButton = screen.getByRole('button', { name: /collapse/i })
    expect(toggleButton).toBeInTheDocument()

    // Click toggle button to collapse
    fireEvent.click(toggleButton)
    expect(sidebarEl).toHaveClass('w-16')

    // Click toggle button to expand again
    fireEvent.click(toggleButton)
    expect(sidebarEl).toHaveClass('w-64')
  })

  it('calls onViewChange with the correct view ID when a menu item is clicked', () => {
    const handleViewChange = vi.fn()
    render(<Sidebar currentView="daily_loop" onViewChange={handleViewChange} />)

    const staffingItem = screen.getByText('People / Staffing')
    fireEvent.click(staffingItem)

    expect(handleViewChange).toHaveBeenCalledWith('staffing')
  })
})
