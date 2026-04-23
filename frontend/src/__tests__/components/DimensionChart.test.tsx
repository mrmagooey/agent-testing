import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import DimensionChart from '../../components/DimensionChart'

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock('recharts', () => ({
  BarChart: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="bar-chart">{children}</div>
  ),
  Bar: () => <div data-testid="bar" />,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
}))

// Mock useTheme
vi.mock('../../hooks/useTheme', () => ({
  useTheme: vi.fn(),
}))

import { useTheme } from '../../hooks/useTheme'
const mockUseTheme = vi.mocked(useTheme)

const sampleData = [
  { category: 'A', score: 0.8 },
  { category: 'B', score: 0.6 },
  { category: 'C', score: 0.9 },
]

beforeEach(() => {
  vi.clearAllMocks()
  mockUseTheme.mockReturnValue({ isDark: false, toggle: vi.fn() })
})

describe('DimensionChart', () => {
  it('renders without crashing', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Score by Category"
      />
    )
    expect(screen.getByTestId('bar-chart')).toBeInTheDocument()
  })

  it('renders the title', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Score by Category"
      />
    )
    expect(screen.getByText('Score by Category')).toBeInTheDocument()
  })

  it('renders title as h3 element', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="My Chart"
      />
    )
    const heading = screen.getByRole('heading', { level: 3 })
    expect(heading).toBeInTheDocument()
    expect(heading.textContent).toBe('My Chart')
  })

  it('renders ResponsiveContainer', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Test"
      />
    )
    expect(screen.getByTestId('responsive-container')).toBeInTheDocument()
  })

  it('renders Bar component', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Test"
      />
    )
    expect(screen.getByTestId('bar')).toBeInTheDocument()
  })

  it('renders with empty data without crashing', () => {
    render(
      <DimensionChart
        data={[]}
        xKey="category"
        yKey="score"
        title="Empty Chart"
      />
    )
    expect(screen.getByText('Empty Chart')).toBeInTheDocument()
  })

  it('renders in dark mode without crashing', () => {
    mockUseTheme.mockReturnValue({ isDark: true, toggle: vi.fn() })
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Dark Chart"
      />
    )
    expect(screen.getByText('Dark Chart')).toBeInTheDocument()
  })

  it('renders with custom color prop without crashing', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Custom Color"
        color="#ff0000"
      />
    )
    expect(screen.getByText('Custom Color')).toBeInTheDocument()
  })

  it('renders with isRatio=false without crashing', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Count Chart"
        isRatio={false}
      />
    )
    expect(screen.getByText('Count Chart')).toBeInTheDocument()
  })

  it('renders with isRatio=true without crashing', () => {
    render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Ratio Chart"
        isRatio={true}
      />
    )
    expect(screen.getByText('Ratio Chart')).toBeInTheDocument()
  })

  it('wraps chart in a bordered card div', () => {
    const { container } = render(
      <DimensionChart
        data={sampleData}
        xKey="category"
        yKey="score"
        title="Card Chart"
      />
    )
    const card = container.querySelector('.bg-card')
    expect(card).toBeInTheDocument()
  })
})
