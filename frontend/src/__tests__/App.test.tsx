import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../hooks/useTheme', () => ({
  useTheme: vi.fn(() => ({ isDark: false, toggle: vi.fn() })),
}))

vi.mock('../pages/Dashboard', () => ({ default: () => <div>Dashboard page</div> }))
vi.mock('../pages/ExperimentNew', () => ({ default: () => <div>ExperimentNew page</div> }))
vi.mock('../pages/ExperimentDetail', () => ({ default: () => <div>ExperimentDetail page</div> }))
vi.mock('../pages/RunDetail', () => ({ default: () => <div>RunDetail page</div> }))
vi.mock('../pages/RunCompare', () => ({ default: () => <div>RunCompare page</div> }))
vi.mock('../pages/CVEDiscovery', () => ({ default: () => <div>CVEDiscovery page</div> }))
vi.mock('../pages/Datasets', () => ({ default: () => <div>Datasets page</div> }))
vi.mock('../pages/DatasetDetail', () => ({ default: () => <div>DatasetDetail page</div> }))
vi.mock('../pages/DatasetSourceView', () => ({ default: () => <div>DatasetSourceView page</div> }))
vi.mock('../pages/Feedback', () => ({ default: () => <div>Feedback page</div> }))
vi.mock('../pages/Findings', () => ({ default: () => <div>Findings page</div> }))
vi.mock('../pages/StrategiesList', () => ({ default: () => <div>StrategiesList page</div> }))
vi.mock('../pages/StrategyViewer', () => ({ default: () => <div>StrategyViewer page</div> }))
vi.mock('../pages/StrategyEditor', () => ({ default: () => <div>StrategyEditor page</div> }))
vi.mock('../pages/ExperimentImport', () => ({ default: () => <div>ExperimentImport page</div> }))
vi.mock('../pages/Settings', () => ({ default: () => <div>Settings page</div> }))
vi.mock('../pages/NotFound', () => ({ default: () => <div>NotFound page</div> }))

import { NavBar } from '../App'

function renderNavBar(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <NavBar />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('NavBar — top-level entries', () => {
  it('renders all 5 top-level entries', () => {
    renderNavBar()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /inputs/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /experiments/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /results/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument()
  })
})

describe('NavBar — Inputs dropdown', () => {
  it('reveals Strategies, Datasets, CVE Discovery when Inputs trigger is clicked', async () => {
    const user = userEvent.setup()
    renderNavBar()
    const inputsTrigger = screen.getByRole('button', { name: /inputs/i })
    await user.click(inputsTrigger)

    await waitFor(() => {
      expect(screen.getByRole('menuitem', { name: /strategies/i })).toBeInTheDocument()
    })
    expect(screen.getByRole('menuitem', { name: /datasets/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /cve discovery/i })).toBeInTheDocument()
  })
})

describe('NavBar — Experiments dropdown', () => {
  it('reveals New Experiment, Import, Compare when Experiments trigger is clicked', async () => {
    const user = userEvent.setup()
    renderNavBar()
    const experimentsTrigger = screen.getByRole('button', { name: /experiments/i })
    await user.click(experimentsTrigger)

    await waitFor(() => {
      expect(screen.getByRole('menuitem', { name: /new experiment/i })).toBeInTheDocument()
    })
    expect(screen.getByRole('menuitem', { name: /import/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /compare/i })).toBeInTheDocument()
  })
})

describe('NavBar — Results dropdown', () => {
  it('reveals Findings, Feedback when Results trigger is clicked', async () => {
    const user = userEvent.setup()
    renderNavBar()
    const resultsTrigger = screen.getByRole('button', { name: /results/i })
    await user.click(resultsTrigger)

    await waitFor(() => {
      expect(screen.getByRole('menuitem', { name: /findings/i })).toBeInTheDocument()
    })
    expect(screen.getByRole('menuitem', { name: /feedback/i })).toBeInTheDocument()
  })
})

describe('NavBar — active route highlighting', () => {
  it('marks the Inputs trigger as active when route is /datasets/discover', () => {
    renderNavBar('/datasets/discover')
    const inputsTrigger = screen.getByRole('button', { name: /inputs/i })
    expect(
      inputsTrigger.classList.contains('text-primary') ||
        inputsTrigger.classList.contains('nav-cursor'),
    ).toBe(true)
  })
})
