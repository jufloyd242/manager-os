import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { CommandCenter } from '../components/CommandCenter'
import { mockCommandRegistry } from '../api/mockData'

describe('CommandCenter', () => {
  it('renders command labels from the mock registry', () => {
    render(<CommandCenter commands={mockCommandRegistry} onRun={vi.fn()} />)
    for (const command of mockCommandRegistry) {
      expect(screen.getByText(command.label)).toBeInTheDocument()
    }
  })

  it('shows a confirmation warning for a command requiring confirmation / likely external risk', () => {
    render(<CommandCenter commands={mockCommandRegistry} onRun={vi.fn()} />)
    const row = screen.getByTestId('command-row-project-docs-fetch')
    expect(row).toHaveTextContent(/requires confirmation/i)
  })

  it('enables Run for a local_safe command', () => {
    render(<CommandCenter commands={mockCommandRegistry} onRun={vi.fn()} />)
    const row = screen.getByTestId('command-row-status')
    const runButton = within(row).getByRole('button', { name: 'Run' })
    expect(runButton).not.toBeDisabled()
  })

  it('disables Run for a blocked command', () => {
    render(<CommandCenter commands={mockCommandRegistry} onRun={vi.fn()} />)
    const row = screen.getByTestId('command-row-demo-reset')
    const runButton = within(row).getByRole('button', { name: 'Run' })
    expect(runButton).toBeDisabled()
  })
})
