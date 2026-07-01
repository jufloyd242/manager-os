import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CommandCenter } from '../components/CommandCenter'
import { mockCommandRegistry } from '../api/mockData'
import type { RunResponse, ValidateResponse } from '../api/client'

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

const LIVE_SINGLE_ID = 'project_docs_fetch_live_single'
const DRY_RUN_ID = 'project_docs_fetch_dry_run'
const BATCH_LIVE_ID = 'project_docs_fetch_batch_live_bounded'

const VALIDATE_SUCCESS: ValidateResponse = {
  ok: true,
  command_id: LIVE_SINGLE_ID,
  risk_level: 'external_bounded',
  external_call_risk: 'likely',
  requires_confirmation: true,
  dry_run_required_before_live: true,
  estimated_input_tokens: 950,
  estimated_output_tokens: null,
  argv_preview: ['project-docs-fetch', '--opportunity-number', 'OPP-1', '--limit', '3', '--timeout', '60'],
  warnings: [],
}

const DRY_RUN_SUCCESS: RunResponse = {
  ok: true,
  run_id: 'run-dry-1',
  status: 'success',
  command_id: DRY_RUN_ID,
  stdout: '[dry-run] would search Drive for OPP-1',
  stderr: null,
  error: null,
  estimated_input_tokens: 950,
  estimated_output_tokens: null,
}

const LIVE_RUN_SUCCESS: RunResponse = {
  ok: true,
  run_id: 'run-live-1',
  status: 'success',
  command_id: LIVE_SINGLE_ID,
  stdout: 'Found 2 documents for OPP-1',
  stderr: null,
  error: null,
  estimated_input_tokens: 950,
  estimated_output_tokens: 120,
}

beforeEach(() => {
  vi.mocked(getCommands).mockReset().mockResolvedValue({ data: mockCommandRegistry, isMock: false })
  vi.mocked(validateCommand).mockReset()
  vi.mocked(runCommand).mockReset()
})

async function fillOpportunityNumber(row: HTMLElement, user: ReturnType<typeof userEvent.setup>) {
  const input = within(row).getByLabelText(/parameter opportunity_number/i)
  await user.type(input, 'OPP-1')
}

describe('project_docs_fetch_live_single guarded flow', () => {
  it('renders a distinct external-call risk warning, unlike a plain local_safe command row', async () => {
    render(<CommandCenter />)
    const liveRow = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)
    expect(within(liveRow).getByTestId('external-call-warning')).toHaveTextContent(/Google Drive via Gemini CLI/i)

    const statusRow = await screen.findByTestId('command-row-status')
    expect(within(statusRow).queryByTestId('external-call-warning')).not.toBeInTheDocument()
  })

  it('disables "Run Live" before any validation has occurred', async () => {
    render(<CommandCenter />)
    const row = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)
    const runLiveButton = within(row).getByRole('button', { name: 'Run Live' })
    expect(runLiveButton).toBeDisabled()
  })

  it('shows estimated tokens and argv preview after a successful validate', async () => {
    vi.mocked(validateCommand).mockResolvedValue({ data: VALIDATE_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)

    await fillOpportunityNumber(row, user)
    await user.click(within(row).getByRole('button', { name: 'Validate' }))

    expect(await within(row).findByText(/argv preview/i)).toBeInTheDocument()
    expect(row).toHaveTextContent('950')
    expect(row).toHaveTextContent(VALIDATE_SUCCESS.argv_preview!.join(' '))
  })

  it('keeps "Run Live" disabled after validation succeeds, until a dry run also succeeds', async () => {
    vi.mocked(validateCommand).mockResolvedValue({ data: VALIDATE_SUCCESS, isMock: false })
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)

    await fillOpportunityNumber(row, user)
    await user.click(within(row).getByRole('button', { name: 'Validate' }))
    await within(row).findByText(/argv preview/i)

    const runLiveButton = within(row).getByRole('button', { name: 'Run Live' })
    expect(runLiveButton).toBeDisabled()
  })

  it('reveals the confirmation step once the dry run succeeds', async () => {
    vi.mocked(validateCommand).mockResolvedValue({ data: VALIDATE_SUCCESS, isMock: false })
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)

    await fillOpportunityNumber(row, user)
    await user.click(within(row).getByRole('button', { name: 'Validate' }))
    await within(row).findByText(/argv preview/i)

    await user.click(within(row).getByRole('button', { name: 'Run Dry Run' }))

    expect(await within(row).findByTestId('confirm-live-run-toggle')).toBeInTheDocument()
  })

  it('confirms and runs live, calling runCommand with confirm=true and the dry run id in params', async () => {
    vi.mocked(validateCommand).mockResolvedValue({ data: VALIDATE_SUCCESS, isMock: false })
    vi.mocked(runCommand).mockImplementation((commandId) => {
      if (commandId === DRY_RUN_ID) return Promise.resolve({ data: DRY_RUN_SUCCESS, isMock: false })
      return Promise.resolve({ data: LIVE_RUN_SUCCESS, isMock: false })
    })
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)

    await fillOpportunityNumber(row, user)
    await user.click(within(row).getByRole('button', { name: 'Validate' }))
    await within(row).findByText(/argv preview/i)
    await user.click(within(row).getByRole('button', { name: 'Run Dry Run' }))
    await within(row).findByTestId('confirm-live-run-toggle')

    await user.click(within(row).getByRole('checkbox'))
    const runLiveButton = within(row).getByRole('button', { name: 'Run Live' })
    expect(runLiveButton).not.toBeDisabled()
    await user.click(runLiveButton)

    expect(await within(row).findByText('Found 2 documents for OPP-1')).toBeInTheDocument()
    expect(runCommand).toHaveBeenCalledWith(
      LIVE_SINGLE_ID,
      expect.objectContaining({ opportunity_number: 'OPP-1', dry_run_run_id: DRY_RUN_SUCCESS.run_id }),
      true,
    )
  })

  it('leaves a blocked/batch-live command fully disabled, unaffected by the new flow', async () => {
    render(<CommandCenter />)
    const batchRow = await screen.findByTestId(`command-row-${BATCH_LIVE_ID}`)
    const runButton = within(batchRow).getByRole('button', { name: 'Run' })
    expect(runButton).toBeDisabled()
    expect(within(batchRow).queryByTestId('external-call-warning')).not.toBeInTheDocument()
    expect(within(batchRow).queryByRole('button', { name: 'Run Live' })).not.toBeInTheDocument()

    const blockedRow = await screen.findByTestId('command-row-demo-reset')
    expect(within(blockedRow).getByRole('button', { name: 'Run' })).toBeDisabled()
  })

  it('shows a visible warning without crashing when validateCommand rejects', async () => {
    vi.mocked(validateCommand).mockRejectedValueOnce(new Error('network error'))
    const user = userEvent.setup()
    render(<CommandCenter />)
    const row = await screen.findByTestId(`command-row-${LIVE_SINGLE_ID}`)

    await fillOpportunityNumber(row, user)
    await user.click(within(row).getByRole('button', { name: 'Validate' }))

    expect(await within(row).findByTestId('validate-error')).toBeInTheDocument()
  })
})
