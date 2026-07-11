import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ActionInbox } from '../components/ActionInbox'
import type { RecommendedAction, ActionGroup, ActionSummary, RunResponse } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    runCommand: vi.fn(),
  }
})

import { runCommand } from '../api/client'

function buildDocGapAction(n: number): RecommendedAction {
  const opp = `OPP-DOCGAP-${String(n).padStart(3, '0')}`
  const client = n === 30 ? 'Acme Corp' : `Client ${n}`
  const project = n === 30 ? 'Acme Corp — Phase 9' : `${client} — Project ${n}`
  return {
    id: `document_gap:${opp}`,
    title: `Fetch missing SOW for ${project}`,
    reason: `Document gap: SOW missing for ${project}`,
    command: `manager-os project-docs-fetch --opportunity-number ${opp}`,
    priority: 'medium',
    source: 'document_gaps',
    entity_type: 'project',
    entity_id: opp,
    primary_command: {
      command_id: 'project_docs_fetch_dry_run',
      params: { opportunity_number: opp },
    },
    secondary_commands: [
      {
        label: 'Print Prompt',
        command_id: 'project_docs_fetch_print_prompt',
        params: { opportunity_number: opp },
      },
      {
        label: 'Run Live Fetch',
        command_id: 'project_docs_fetch_live_single',
        params: { opportunity_number: opp },
        requires_confirmation: true,
        requires_successful_dry_run: true,
      },
    ],
  }
}

// 45 document-gap actions; index n=30 is the unique "Acme Corp" one used for
// search-filter assertions, all others are "Client {n}".
const DOCUMENT_GAP_ACTIONS: RecommendedAction[] = Array.from({ length: 45 }, (_, i) => buildDocGapAction(i + 1))

const PEOPLE_ACTION: RecommendedAction = {
  title: 'Schedule 1:1 with Jordan Lee',
  reason: 'No 1:1 recorded in 18 days',
  command: 'manager-os meeting-prep --meeting jordan-lee-1-1',
  priority: 'medium',
  source: 'people_staffing',
}

const ACTION_GROUPS: ActionGroup[] = [
  {
    id: 'document_gaps',
    title: 'Document Gaps',
    source: 'document_gaps',
    count: 45,
    priority: 'high',
    summary: '45 projects missing required documents',
    default_visible_count: 5,
    actions: DOCUMENT_GAP_ACTIONS,
  },
  {
    id: 'people_staffing',
    title: 'People / Staffing',
    source: 'people_staffing',
    count: 1,
    priority: 'medium',
    summary: '1 staffing signal needs attention',
    default_visible_count: 5,
    actions: [PEOPLE_ACTION],
  },
]

const ACTION_SUMMARY: ActionSummary = {
  total: 46,
  by_source: { document_gaps: 45, people_staffing: 1 },
  by_priority: { high: 0, medium: 46, low: 0 },
  executable: 45,
  informational: 1,
}

const DRY_RUN_SUCCESS: RunResponse = {
  ok: true,
  run_id: 'run-dry-900',
  status: 'success',
  command_id: 'project_docs_fetch_dry_run',
  stdout: '[dry-run] would search Drive for OPP-DOCGAP-001',
  stderr: null,
  error: null,
  estimated_input_tokens: 400,
  estimated_output_tokens: null,
}

beforeEach(() => {
  vi.mocked(runCommand).mockReset()
})

describe('ActionInbox', () => {
  it('renders the total action count from action_summary', () => {
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    expect(screen.getByTestId('action-inbox-header')).toHaveTextContent('46')
  })

  it('renders the document_gaps group with its title and summary', () => {
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    expect(screen.getByText('Document Gaps')).toBeInTheDocument()
    expect(screen.getByTestId('action-group-document_gaps')).toBeInTheDocument()
  })

  it('shows the document_gaps group count in its header/summary text', () => {
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    expect(screen.getByTestId('action-group-document_gaps')).toHaveTextContent('45')
  })

  it('initially renders only default_visible_count actions for document_gaps', () => {
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    expect(within(group).getAllByTestId('action-item')).toHaveLength(5)
  })

  it('clicking "Show all" reveals all actions in the group', async () => {
    const user = userEvent.setup()
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    await user.click(within(group).getByRole('button', { name: /show all/i }))
    expect(within(group).getAllByTestId('action-item')).toHaveLength(45)
  })

  it('clicking "Show less" after expanding collapses back to the default count', async () => {
    const user = userEvent.setup()
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    await user.click(within(group).getByRole('button', { name: /show all/i }))
    await user.click(within(group).getByRole('button', { name: /show less/i }))
    expect(within(group).getAllByTestId('action-item')).toHaveLength(5)
  })

  it('typing an OppID into the document_gaps search box filters to matches only', async () => {
    const user = userEvent.setup()
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    const search = within(group).getByRole('textbox', { name: /search/i })
    await user.type(search, 'OPP-DOCGAP-030')
    const items = within(group).getAllByTestId('action-item')
    expect(items).toHaveLength(1)
    expect(items[0]).toHaveTextContent('OPP-DOCGAP-030')
  })

  it('typing a client/project name into the search box filters similarly', async () => {
    const user = userEvent.setup()
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    const search = within(group).getByRole('textbox', { name: /search/i })
    await user.type(search, 'Acme Corp')
    expect(within(group).getAllByTestId('action-item')).toHaveLength(1)
    expect(within(group).getAllByText(/Acme Corp/).length).toBeGreaterThan(0)
  })

  it('the Dry Run button for a document-gap action inside the inbox still calls runCommand correctly', async () => {
    vi.mocked(runCommand).mockResolvedValue({ data: DRY_RUN_SUCCESS, isMock: false })
    const user = userEvent.setup()
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    const dryRunButtons = within(group).getAllByRole('button', { name: 'Dry Run Fetch' })
    await user.click(dryRunButtons[0])

    expect(runCommand).toHaveBeenCalledWith(
      'project_docs_fetch_dry_run',
      { opportunity_number: 'OPP-DOCGAP-001' },
      false,
    )
  })

  it('the guarded Live Fetch button inside the inbox is disabled before a successful dry run', () => {
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    const group = screen.getByTestId('action-group-document_gaps')
    const liveButtons = within(group).getAllByRole('button', { name: 'Run Live Fetch' })
    expect(liveButtons[0]).toBeDisabled()
  })

  it('has no batch-live / run-all button anywhere in the Action Inbox', () => {
    render(
      <ActionInbox actionSummary={ACTION_SUMMARY} actionGroups={ACTION_GROUPS} recommendedActions={[]} />,
    )
    expect(screen.queryByRole('button', { name: /batch/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run all/i })).not.toBeInTheDocument()
  })

  it('falls back to client-side grouping by source when action_groups is omitted', () => {
    render(
      <ActionInbox
        actionSummary={undefined}
        actionGroups={undefined}
        recommendedActions={[PEOPLE_ACTION, DOCUMENT_GAP_ACTIONS[0]]}
      />,
    )
    expect(screen.getAllByText(/document gaps/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/people staffing/i).length).toBeGreaterThan(0)
    expect(screen.getByTestId('action-inbox-header')).toHaveTextContent('2')
  })
})
