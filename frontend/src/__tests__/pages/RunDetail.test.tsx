import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RunDetail from '../../pages/RunDetail'
import type { Run, Finding, ToolCall, Message } from '../../api/client'

vi.mock('../../api/client', () => ({
  getRun: vi.fn(),
  getExperiment: vi.fn(),
  getFileContent: vi.fn(),
  reclassifyFinding: vi.fn(),
}))

// Mock heavy sub-components
vi.mock('../../components/CodeViewer', () => ({
  default: ({ content }: { content: string }) => <pre data-testid="code-viewer">{content}</pre>,
}))
vi.mock('../../components/ConversationViewer', () => ({
  default: () => <div data-testid="conversation-viewer" />,
}))
vi.mock('../../components/DownloadButton', () => ({
  default: ({ label }: { label?: string }) => <button>{label ?? 'Download'}</button>,
}))
vi.mock('../../components/PromptInjectionViewer', () => ({
  default: () => <div data-testid="prompt-injection-viewer" />,
}))

import { getRun, getExperiment, reclassifyFinding } from '../../api/client'
const mockGetRun = vi.mocked(getRun)
const mockGetExperiment = vi.mocked(getExperiment)
const mockReclassifyFinding = vi.mocked(reclassifyFinding)

type RunFull = Run & { findings: Finding[]; tool_calls: ToolCall[]; messages: Message[] }

function makeRun(overrides: Partial<RunFull> = {}): RunFull {
  return {
    run_id: 'run-abc',
    experiment_id: 'exp-xyz',
    model: 'gpt-4o',
    strategy: 'zero_shot',
    tool_variant: 'none',
    profile: 'default',
    verification: 'none',
    status: 'completed',
    precision: 0.85,
    recall: 0.75,
    f1: 0.80,
    fpr: 0.05,
    cost_usd: 0.1234,
    duration_seconds: 42,
    findings: [],
    tool_calls: [],
    messages: [],
    ...overrides,
  }
}

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f-1',
    run_id: 'run-abc',
    experiment_id: 'exp-xyz',
    title: 'SQL Injection',
    description: 'User input not sanitized.',
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    file_path: 'src/main.py',
    line_start: 10,
    ...overrides,
  }
}

function makeToolCall(overrides: Partial<ToolCall> = {}): ToolCall {
  return {
    tool_name: 'read_file',
    input: { path: 'src/main.py' },
    timestamp: new Date().toISOString(),
    flagged: false,
    ...overrides,
  }
}

function renderPage(experimentId = 'exp-xyz', runId = 'run-abc') {
  return render(
    <MemoryRouter initialEntries={[`/experiments/${experimentId}/runs/${runId}`]}>
      <Routes>
        <Route path="/experiments/:experimentId/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetExperiment.mockResolvedValue({
    experiment_id: 'exp-xyz',
    status: 'completed',
    dataset: 'ds-test',
    created_at: new Date().toISOString(),
    total_runs: 1,
    completed_runs: 1,
    running_runs: 0,
    pending_runs: 0,
    failed_runs: 0,
    total_cost_usd: 0.1,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RunDetail — loading and error states', () => {
  it('shows loading spinner while fetching run', () => {
    mockGetRun.mockReturnValue(new Promise(() => {}))
    renderPage()
    // PageLoadingSpinner renders without the main content
    expect(screen.queryByRole('heading', { name: /run-abc/ })).not.toBeInTheDocument()
  })

  it('shows error when getRun rejects', async () => {
    mockGetRun.mockRejectedValue(new Error('Run not found'))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Run not found/)).toBeInTheDocument()
    })
  })
})

describe('RunDetail — run rendered', () => {
  it('renders run_id as heading', async () => {
    mockGetRun.mockResolvedValue(makeRun())
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /run-abc/ })).toBeInTheDocument()
    })
  })

  it('shows experiment ID in header', async () => {
    mockGetRun.mockResolvedValue(makeRun())
    renderPage()
    await waitFor(() => {
      // The component renders exp-xyz in both the breadcrumb link and the header paragraph.
      // Use getAllByText to handle the multiple matches gracefully.
      expect(screen.getAllByText(/exp-xyz/).length).toBeGreaterThan(0)
    })
  })

  it('shows model name in metadata', async () => {
    mockGetRun.mockResolvedValue(makeRun({ model: 'claude-3-5-sonnet' }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('claude-3-5-sonnet')).toBeInTheDocument()
    })
  })

  it('displays F1 metric', async () => {
    mockGetRun.mockResolvedValue(makeRun({ f1: 0.8 }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('0.800')).toBeInTheDocument()
    })
  })

  it('shows cost badge', async () => {
    mockGetRun.mockResolvedValue(makeRun({ cost_usd: 0.1234 }))
    renderPage()
    await waitFor(() => {
      // Cost appears in both the metadata dl and the CostBadge component; use getAllByText.
      expect(screen.getAllByText('$0.1234').length).toBeGreaterThan(0)
    })
  })

  it('shows duration in metadata dl', async () => {
    mockGetRun.mockResolvedValue(makeRun({ duration_seconds: 42 }))
    renderPage()
    await waitFor(() => {
      // Duration appears in both the metadata dl and the CostBadge span; use getAllByText.
      expect(screen.getAllByText('42s').length).toBeGreaterThan(0)
    })
  })
})

describe('RunDetail — empty findings', () => {
  it('shows EmptyState when run has no findings', async () => {
    mockGetRun.mockResolvedValue(makeRun({ findings: [] }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('No findings')).toBeInTheDocument()
    })
  })
})

describe('RunDetail — findings table', () => {
  it('renders findings table with correct column headers', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      findings: [makeFinding()],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: /status/i })).toBeInTheDocument()
      expect(screen.getByRole('columnheader', { name: /title/i })).toBeInTheDocument()
      expect(screen.getByRole('columnheader', { name: /severity/i })).toBeInTheDocument()
    })
  })

  it('shows finding title in table', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      findings: [makeFinding({ title: 'XSS Attack Vector' })],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('XSS Attack Vector')).toBeInTheDocument()
    })
  })

  it('expands finding row to show description on click', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      findings: [makeFinding({ title: 'SQL Injection', description: 'Detailed description here.' })],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('SQL Injection')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('SQL Injection'))

    await waitFor(() => {
      expect(screen.getByText('Detailed description here.')).toBeInTheDocument()
    })
  })

  it('shows Reclassify button for FP findings when expanded', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      findings: [makeFinding({ match_status: 'fp', title: 'False Positive Finding' })],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('False Positive Finding')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('False Positive Finding'))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /reclassify as unlabeled real/i })).toBeInTheDocument()
    })
  })
})

describe('RunDetail — severity/match filters', () => {
  it('filters findings by severity', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      findings: [
        makeFinding({ finding_id: 'f-1', title: 'High Finding', severity: 'high' }),
        makeFinding({ finding_id: 'f-2', title: 'Low Finding', severity: 'low' }),
      ],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('High Finding')).toBeInTheDocument()
    })

    // Change severity filter to 'high'
    const severitySelect = screen.getByDisplayValue('All severities')
    fireEvent.change(severitySelect, { target: { value: 'high' } })

    await waitFor(() => {
      expect(screen.getByText('High Finding')).toBeInTheDocument()
      expect(screen.queryByText('Low Finding')).not.toBeInTheDocument()
    })
  })

  it('shows count of filtered vs total findings', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      findings: [
        makeFinding({ finding_id: 'f-1', severity: 'high' }),
        makeFinding({ finding_id: 'f-2', severity: 'low' }),
      ],
    }))
    renderPage()
    await waitFor(() => {
      // "2 of 2" when no filter
      expect(screen.getByText('2 of 2')).toBeInTheDocument()
    })
  })
})

describe('RunDetail — tool call audit', () => {
  it('shows Tool Call Audit section heading', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      tool_calls: [makeToolCall()],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Tool Call Audit \(1\)/i })).toBeInTheDocument()
    })
  })

  it('shows tool name in audit table', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      tool_calls: [makeToolCall({ tool_name: 'read_file' })],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('read_file')).toBeInTheDocument()
    })
  })

  it('flags tool calls containing URLs', async () => {
    mockGetRun.mockResolvedValue(makeRun({
      tool_calls: [makeToolCall({ input: { url: 'https://evil.com/exfiltrate' } })],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('⚠ URL')).toBeInTheDocument()
    })
  })
})

describe('RunDetail — conversation collapsible', () => {
  it('shows Conversation Transcript collapsible closed by default', async () => {
    mockGetRun.mockResolvedValue(makeRun({ messages: [] }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Conversation Transcript/i })).toBeInTheDocument()
    })
    // Content is hidden by default
    expect(screen.queryByTestId('conversation-viewer')).not.toBeInTheDocument()
  })

  it('expands Conversation Transcript on click', async () => {
    mockGetRun.mockResolvedValue(makeRun({ messages: [] }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Conversation Transcript/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Conversation Transcript/i }))

    expect(screen.getByTestId('conversation-viewer')).toBeInTheDocument()
  })
})

describe('RunDetail — reclassify action', () => {
  it('calls reclassifyFinding when Reclassify button is clicked', async () => {
    mockReclassifyFinding.mockResolvedValue(undefined)
    mockGetRun.mockResolvedValue(makeRun({
      findings: [makeFinding({ finding_id: 'f-fp', match_status: 'fp', title: 'FP Finding' })],
    }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('FP Finding')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('FP Finding'))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /reclassify as unlabeled real/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /reclassify as unlabeled real/i }))

    await waitFor(() => {
      expect(mockReclassifyFinding).toHaveBeenCalledWith(
        'exp-xyz',
        'run-abc',
        'f-fp',
        'unlabeled_real',
        '',
      )
    })
  })
})
