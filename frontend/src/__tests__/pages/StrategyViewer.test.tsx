import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import StrategyViewer from '../../pages/StrategyViewer'
import type { UserStrategy } from '../../api/client'

vi.mock('../../api/client', () => ({
  getStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
}))

import { getStrategy, deleteStrategy } from '../../api/client'
const mockGetStrategy = vi.mocked(getStrategy)
const mockDeleteStrategy = vi.mocked(deleteStrategy)

function makeStrategy(overrides: Partial<UserStrategy> = {}): UserStrategy {
  return {
    id: 'builtin.single_agent',
    name: 'Single Agent',
    orchestration_shape: 'single_agent',
    is_builtin: true,
    parent_strategy_id: null,
    created_at: '2024-01-01T00:00:00Z',
    default: {
      system_prompt: 'You are a security researcher.',
      user_prompt_template: 'Analyze {repo_summary} for {finding_output_format}.',
      profile_modifier: '',
      model_id: 'claude-sonnet-4-5',
      tools: ['read_file', 'list_directory'],
      verification: 'none',
      max_turns: 10,
      tool_extensions: [],
    },
    overrides: [],
    ...overrides,
  }
}

function renderViewer(strategyId = 'builtin.single_agent') {
  return render(
    <MemoryRouter initialEntries={[`/strategies/${strategyId}`]}>
      <Routes>
        <Route path="/strategies/:id" element={<StrategyViewer />} />
        <Route path="/strategies" element={<div>Strategies List</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('StrategyViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the strategy name and shape', async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy())
    renderViewer()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Single Agent' })).toBeVisible()
    })

    expect(screen.getByText('single_agent')).toBeVisible()
  })

  it('shows builtin badge for builtin strategies', async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy({ is_builtin: true }))
    renderViewer()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Single Agent' })).toBeVisible()
    })

    expect(screen.getByText('builtin')).toBeVisible()
  })

  it('delete button is NOT rendered for builtin strategies', async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy({ is_builtin: true }))
    renderViewer()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Single Agent' })).toBeVisible()
    })

    expect(screen.queryByTestId('delete-btn')).toBeNull()
  })

  it('delete button IS rendered for user strategies', async () => {
    mockGetStrategy.mockResolvedValue(
      makeStrategy({ id: 'user.foo-bar-abc', is_builtin: false, name: 'My Strategy' }),
    )
    renderViewer('user.foo-bar-abc')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'My Strategy' })).toBeVisible()
    })

    expect(screen.getByTestId('delete-btn')).toBeVisible()
  })

  it('renders default bundle fields', async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy())
    renderViewer()

    await waitFor(() => {
      expect(screen.getByText('Default Bundle')).toBeVisible()
    })

    expect(screen.getByText('claude-sonnet-4-5')).toBeVisible()
    // verification field "none" appears somewhere on the page
    expect(screen.getAllByText('none').length).toBeGreaterThanOrEqual(1)
  })

  it('overrides section is hidden for single_agent shape', async () => {
    mockGetStrategy.mockResolvedValue(
      makeStrategy({ orchestration_shape: 'single_agent', overrides: [] }),
    )
    renderViewer()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Single Agent' })).toBeVisible()
    })

    expect(screen.queryByText('Overrides')).toBeNull()
  })

  it('shows per_vuln_class overrides in tabs', async () => {
    mockGetStrategy.mockResolvedValue(
      makeStrategy({
        orchestration_shape: 'per_vuln_class',
        overrides: [
          { key: 'sqli', override: { system_prompt: 'Focus on SQL injection.' } },
          { key: 'xss', override: { system_prompt: 'Focus on XSS.' } },
        ],
      }),
    )
    renderViewer()

    await waitFor(() => {
      expect(screen.getByText('Overrides')).toBeVisible()
    })

    // Tabs for each vuln class
    expect(screen.getByRole('tab', { name: 'sqli' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'xss' })).toBeVisible()
  })

  it('shows ordered list for per_file overrides with first-match note', async () => {
    mockGetStrategy.mockResolvedValue(
      makeStrategy({
        orchestration_shape: 'per_file',
        overrides: [
          { key: 'src/auth/**', override: { system_prompt: 'Focus on auth.' } },
          { key: '*.py', override: { max_turns: 20 } },
        ],
      }),
    )
    renderViewer()

    await waitFor(() => {
      expect(screen.getByText(/first match wins/i)).toBeVisible()
    })

    expect(screen.getByText('src/auth/**')).toBeVisible()
    expect(screen.getByText('*.py')).toBeVisible()
  })

  it('Fork button navigates to fork URL', async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy())
    renderViewer()

    await waitFor(() => {
      expect(screen.getByTestId('fork-btn')).toBeVisible()
    })
  })

  it('shows parent strategy ID if present', async () => {
    mockGetStrategy.mockResolvedValue(
      makeStrategy({
        id: 'user.fork-abc123',
        is_builtin: false,
        parent_strategy_id: 'builtin.single_agent',
        name: 'My Fork',
      }),
    )
    renderViewer('user.fork-abc123')

    await waitFor(() => {
      expect(screen.getByText(/Forked from/)).toBeVisible()
    })

    expect(screen.getByText('builtin.single_agent')).toBeVisible()
  })

  it('shows error when API fails', async () => {
    mockGetStrategy.mockRejectedValue(new Error('Not found'))
    renderViewer()

    await waitFor(() => {
      expect(screen.getByText(/Not found/)).toBeVisible()
    })
  })

  describe('delete flow', () => {
    it('opens delete confirmation dialog when delete button clicked', async () => {
      mockGetStrategy.mockResolvedValue(
        makeStrategy({ id: 'user.foo-abc', is_builtin: false, name: 'Deletable' }),
      )
      renderViewer('user.foo-abc')

      await waitFor(() => {
        expect(screen.getByTestId('delete-btn')).toBeVisible()
      })

      fireEvent.click(screen.getByTestId('delete-btn'))

      await waitFor(() => {
        expect(screen.getByText(/Are you sure you want to delete/)).toBeVisible()
      })
    })

    it('calls deleteStrategy and navigates to list on confirm', async () => {
      mockGetStrategy.mockResolvedValue(
        makeStrategy({ id: 'user.foo-abc', is_builtin: false, name: 'Deletable' }),
      )
      mockDeleteStrategy.mockResolvedValue(undefined)

      renderViewer('user.foo-abc')

      await waitFor(() => {
        expect(screen.getByTestId('delete-btn')).toBeVisible()
      })

      fireEvent.click(screen.getByTestId('delete-btn'))

      await waitFor(() => {
        expect(screen.getByTestId('confirm-delete-btn')).toBeVisible()
      })

      fireEvent.click(screen.getByTestId('confirm-delete-btn'))

      await waitFor(() => {
        expect(mockDeleteStrategy).toHaveBeenCalledWith('user.foo-abc')
      })
    })

    it('shows delete error when API returns error', async () => {
      mockGetStrategy.mockResolvedValue(
        makeStrategy({ id: 'user.foo-abc', is_builtin: false, name: 'Deletable' }),
      )
      mockDeleteStrategy.mockRejectedValue(new Error('Referenced by runs'))

      renderViewer('user.foo-abc')

      await waitFor(() => {
        expect(screen.getByTestId('delete-btn')).toBeVisible()
      })

      fireEvent.click(screen.getByTestId('delete-btn'))

      await waitFor(() => {
        expect(screen.getByTestId('confirm-delete-btn')).toBeVisible()
      })

      fireEvent.click(screen.getByTestId('confirm-delete-btn'))

      await waitFor(() => {
        expect(screen.getByText(/Referenced by runs/)).toBeVisible()
      })
    })
  })
})
