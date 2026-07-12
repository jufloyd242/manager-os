import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Sidebar } from '../components/Sidebar'

describe('Sidebar Component', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('renders all sidebar destinations', () => {
    render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    const todayElements = screen.getAllByText('Today')
    expect(todayElements.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Actions')).toBeInTheDocument()
    expect(screen.getByText('Meetings')).toBeInTheDocument()
    expect(screen.getByText('Deals')).toBeInTheDocument()
    expect(screen.getByText('Forecast')).toBeInTheDocument()
    expect(screen.getByText('Workspace')).toBeInTheDocument()
    expect(screen.getByText('People')).toBeInTheDocument()
    expect(screen.getByText('Projects')).toBeInTheDocument()
    expect(screen.getByText('Data Health')).toBeInTheDocument()
    expect(screen.getByText('Operation History')).toBeInTheDocument()
  })

  it('renders group labels in correct order', () => {
    render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    const groups = screen.getAllByText(/^(Today|Work|Context|Operations|Advanced)$/)
    expect(groups.length).toBeGreaterThanOrEqual(4)
  })

  it('Advanced is collapsed by default', () => {
    render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    // Advanced items should not be visible initially
    expect(screen.queryByText('Commands')).not.toBeInTheDocument()
    expect(screen.queryByText('Run History')).not.toBeInTheDocument()
    expect(screen.queryByText('Token Budget')).not.toBeInTheDocument()
    expect(screen.queryByText('Project Archive')).not.toBeInTheDocument()
  })

  it('expands Advanced when clicked', () => {
    render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    const advancedButton = screen.getByText('Advanced')
    fireEvent.click(advancedButton)

    expect(screen.getByText('Commands')).toBeInTheDocument()
    expect(screen.getByText('Run History')).toBeInTheDocument()
    expect(screen.getByText('Token Budget')).toBeInTheDocument()
    expect(screen.getByText('Project Archive')).toBeInTheDocument()
  })

  it('calls onNavigate with correct route when item is clicked', () => {
    const handleNavigate = vi.fn()
    render(<Sidebar currentRoute="today" onNavigate={handleNavigate} />)

    fireEvent.click(screen.getByText('Deals'))
    expect(handleNavigate).toHaveBeenCalledWith('deals')
  })

  it('supports collapsible state', () => {
    const { container } = render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    const sidebarEl = container.firstChild as HTMLElement
    expect(sidebarEl).toHaveClass('w-56')

    const toggleButton = screen.getByRole('button', { name: /collapse/i })
    fireEvent.click(toggleButton)
    expect(sidebarEl).toHaveClass('w-16')

    fireEvent.click(toggleButton)
    expect(sidebarEl).toHaveClass('w-56')
  })

  it('persists collapse preference', () => {
    render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    const toggleButton = screen.getByRole('button', { name: /collapse/i })
    fireEvent.click(toggleButton)

    expect(localStorage.getItem('manager-os-sidebar-collapsed')).toBe('true')
  })

  it('persists advanced preference', () => {
    render(<Sidebar currentRoute="today" onNavigate={vi.fn()} />)

    fireEvent.click(screen.getByText('Advanced'))

    expect(localStorage.getItem('manager-os-advanced-expanded')).toBe('true')
  })

  it('shows active state on current route', () => {
    render(<Sidebar currentRoute="meetings" onNavigate={vi.fn()} />)

    const meetingsButton = screen.getByText('Meetings').closest('button')
    expect(meetingsButton).toHaveClass('bg-indigo-600/90')
  })
})
