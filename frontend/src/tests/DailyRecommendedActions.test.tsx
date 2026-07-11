import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RecommendedActionCard } from '../components/RecommendedActionCard'
import type { RecommendedAction, RunResponse } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    runCommand: vi.fn(),
  }
})

import { runCommand } from '../api/client'

const DOC_GAP_ACTION: RecommendedAction = {
  id: 'doc-gap-initech-discovery-sow',
  title: 'Fetch missing SOW for Initech — Discovery',
  reason: 'Document gap: SOW missing for Initech — Discovery',
  command: 'manager-os project-docs-fetch --opportunity-number OPP-INITECH-004',
  priority: 'medium',
  source: 'document_gaps',
  entity_type: 'project',
  entity_id: 'OPP-INITECH-004',
  primary_command: {
    command_id: 'project_docs_fetch_dry_run',
    params: { opportunity_number: 'OPP-INITECH-004' },
  },
  secondary_commands: [
    {
      label: 'Print Prompt',
      command_id: 'project_docs_fetch_print_prompt',
      params: { opportunity_number: 'OPP-INITECH-004' },
    },
    {
      label: 'Run Live Fetch',
      command_id: 'project_docs_fetch_live_single',
      params: { opportunity_number: 'OPP-INITECH-004' },
      requires_confirmation: true,
      requires_successful_dry_run: true,
    },
  ],
}

const INFORMATIONAL_ACTION: RecommendedAction = {
  title: 'Schedule 1:1 with Jordan Lee',
  reason: 'No 1:1 recorded in 18 days',
  command: 'manager-os meeting-prep --meeting jordan-lee-1-1',
  priority: 'medium',
  source: 'people_staffing',
}

const DRY_RUN_SUCCESS: RunResponse = {
  ok: true,
  run_id: 'run-dry-500',
  status: 'success',
  command_id: 'project_docs_fetch_dry_run',
  stdout: '[dry-run] would search Drive for OPP-INITECH-004',
  stderr: null,
  error: null,
  estimated_input_tokens: 400,
  estimated_output_tokens: null,
}

const PRINT_PROMPT_SUCCESS: RunResponse = {
  ok: true,
  run_id: 'run-print-501',
  status: 'success',
  command_id: 'project_docs_fetch_print_prompt',
  stdout: 'Search Drive for OPP-INITECH-004 metadata only...',
  stderr: null,
  error: null,
  estimated_input_tokens: 400,
  estimated_output_tokens: null,
}

const LIVE_RUN_SUCCESS: RunResponse = {
  ok: true,
  run_id: 'run-live-502',
  status: 'success',
  command_id: 'project_docs_fetch_live_single',
  stdout: 'Found 1 document for OPP-INITECH-004',
  stderr: null,
  error: null,
  estimated_input_tokens: 400,
  estimated_output_tokens: 80,
}

beforeEach(() => {
  vi.mocked(runCommand).mockReset()
})

describe('RecommendedActionCard — document-gap structured actions', () => {
  it('renders a "Dry Run Fetch" button for a document-gap action with primary_command', () => {
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)
    expect(screen.getByRole('button', { name: 'Dry Run Fetch' })).toBeInTheDocument()
  })

  it('renders a "Print Prompt" button', () => {
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)
    expect(screen.getByRole('button', { name: 'Print Prompt' })).toBeInTheDocument()
  })

  it('renders a "Run Live Fetch" button, disabled initially', () => {
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)
    expect(screen.getByRole('button', { name: 'Run Live Fetch' })).toBeDisabled()
  })

  it('clicking "Dry Run Fetch" calls runCommand with the primary command id and opportunity_number param', async () => {
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)

    await user.click(screen.getByRole('button', { name: 'Dry Run Fetch' }))

    expect(runCommand).toHaveBeenCalledWith(
      'project_docs_fetch_dry_run',
      { opportunity_number: 'OPP-INITECH-004' },
      false,
    )
  })

  it('enables the Run Live Fetch confirmation step after a successful dry run', async () => {
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)

    await user.click(screen.getByRole('button', { name: 'Dry Run Fetch' }))

    expect(await screen.findByTestId('confirm-live-run-toggle')).toBeInTheDocument()
  })

  it('confirming and running live calls runCommand with confirm=true and the dry run id in params', async () => {
    vi.mocked(runCommand).mockImplementation((commandId) => {
      if (commandId === 'project_docs_fetch_dry_run') return Promise.resolve({ data: DRY_RUN_SUCCESS, isMock: false })
      return Promise.resolve({ data: LIVE_RUN_SUCCESS, isMock: false })
    })
    const user = userEvent.setup()
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)

    await user.click(screen.getByRole('button', { name: 'Dry Run Fetch' }))
    await screen.findByTestId('confirm-live-run-toggle')
    await user.click(screen.getByRole('checkbox'))

    const runLiveButton = screen.getByRole('button', { name: 'Run Live Fetch' })
    expect(runLiveButton).not.toBeDisabled()
    await user.click(runLiveButton)

    expect(await screen.findByText('Found 1 document for OPP-INITECH-004')).toBeInTheDocument()
    expect(runCommand).toHaveBeenCalledWith(
      'project_docs_fetch_live_single',
      expect.objectContaining({ opportunity_number: 'OPP-INITECH-004', dry_run_run_id: DRY_RUN_SUCCESS.run_id }),
      true,
    )
  })

  it('clicking "Print Prompt" calls runCommand with the print-prompt command id and correct params', async () => {
    vi.mocked(runCommand).mockResolvedValue({ data: PRINT_PROMPT_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)

    await user.click(screen.getByRole('button', { name: 'Print Prompt' }))

    expect(runCommand).toHaveBeenCalledWith(
      'project_docs_fetch_print_prompt',
      { opportunity_number: 'OPP-INITECH-004' },
      false,
    )
    expect(await screen.findByText(/Search Drive for OPP-INITECH-004/)).toBeInTheDocument()
  })

  it('renders no buttons for an action with no primary_command, just plain text', () => {
    render(<RecommendedActionCard action={INFORMATIONAL_ACTION} />)

    expect(screen.getByText(INFORMATIONAL_ACTION.title)).toBeInTheDocument()
    expect(screen.getByText((_content, node) => {
      return node?.textContent === `Reason: ${INFORMATIONAL_ACTION.reason}`
    })).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('triggers the Recent Runs refresh callback after a dry run completes', async () => {
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const onRunRecorded = vi.fn()
    const user = userEvent.setup()
    render(<RecommendedActionCard action={DOC_GAP_ACTION} onRunRecorded={onRunRecorded} />)

    await user.click(screen.getByRole('button', { name: 'Dry Run Fetch' }))

    expect(await screen.findByTestId('confirm-live-run-toggle')).toBeInTheDocument()
    expect(onRunRecorded).toHaveBeenCalledWith(expect.objectContaining({ run_id: DRY_RUN_SUCCESS.run_id }))
  })

  it('shows stdout in the UI after a dry run completes', async () => {
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)

    await user.click(screen.getByRole('button', { name: 'Dry Run Fetch' }))

    expect(await screen.findByText(DRY_RUN_SUCCESS.stdout!)).toBeInTheDocument()
  })

  it('shows a risk/token warning near the Run Live Fetch button', () => {
    render(<RecommendedActionCard action={DOC_GAP_ACTION} />)
    expect(screen.getByTestId('external-call-warning')).toBeInTheDocument()
  })
})
