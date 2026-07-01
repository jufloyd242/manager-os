import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CommandCenter } from '../components/CommandCenter'
import { mockCommandRegistry } from '../api/mockData'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getCommands: vi.fn(),
    validateCommand: vi.fn(),
    runCommand: vi.fn(),
  }
})

import { getCommands, validateCommand, runCommand } from '../api/client'

beforeEach(() => {
  vi.mocked(getCommands).mockReset().mockResolvedValue({ data: mockCommandRegistry, isMock: false })
  vi.mocked(validateCommand).mockReset()
  vi.mocked(runCommand).mockReset()
})

describe('CommandCenter', () => {
  it('fetches and renders command labels from the registry', async () => {
    render(<CommandCenter />)
    for (const command of mockCommandRegistry) {
      expect(await screen.findByText(command.label)).toBeInTheDocument()
    }
  })

  it('shows a confirmation warning for a command requiring confirmation / likely external risk', async () => {
    render(<CommandCenter />)
    const row = await screen.findByTestId('command-row-project-docs-fetch')
    expect(row).toHaveTextContent(/requires confirmation/i)
  })

  it('enables Run for a local_safe command with no confirmation required', async () => {
    render(<CommandCenter />)
    const row = await screen.findByTestId('command-row-status')
    const runButton = within(row).getByRole('button', { name: 'Run' })
    expect(runButton).not.toBeDisabled()
  })

  it('disables Run for a blocked command', async () => {
    render(<CommandCenter />)
    const row = await screen.findByTestId('command-row-demo-reset')
    const runButton = within(row).getByRole('button', { name: 'Run' })
    expect(runButton).toBeDisabled()
  })

  it('disables Run for an external_bounded command requiring confirmation (not freely runnable)', async () => {
    render(<CommandCenter />)
    const row = await screen.findByTestId('command-row-project-docs-fetch')
    const runButton = within(row).getByRole('button', { name: 'Run' })
    expect(runButton).toBeDisabled()
  })

  it('validates a safe command and shows the estimated tokens / argv preview', async () => {
    vi.mocked(validateCommand).mockResolvedValue({
      data: {
        ok: true,
        argv_preview: ['status'],
        risk_level: 'local_safe',
        external_call_risk: 'none',
        estimated_input_tokens: 123,
        warnings: [],
        requires_confirmation: false,
      },
      isMock: false,
    })
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId('command-row-status')

    await user.click(within(row).getByRole('button', { name: 'Validate' }))

    expect(await within(row).findByText(/argv preview/i)).toBeInTheDocument()
    expect(row).toHaveTextContent('123')
  })

  it('runs a safe command and displays stdout', async () => {
    vi.mocked(runCommand).mockResolvedValue({
      data: {
        ok: true,
        run_id: 'run-test-1',
        status: 'success',
        command_id: 'status',
        stdout: 'all systems fresh',
        stderr: null,
        error: null,
        estimated_input_tokens: null,
        estimated_output_tokens: null,
      },
      isMock: false,
    })
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId('command-row-status')

    await user.click(within(row).getByRole('button', { name: 'Run' }))

    expect(await within(row).findByText('all systems fresh')).toBeInTheDocument()
  })

  it('falls back to mock commands and shows an offline indicator when the API is unreachable', async () => {
    vi.mocked(getCommands).mockRejectedValueOnce(new Error('network error'))

    render(<CommandCenter />)

    expect(await screen.findByTestId('command-center-mock-indicator')).toBeInTheDocument()
  })
})

