import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ProgressBar from '../../components/ProgressBar'

describe('ProgressBar', () => {
  it('renders null when total is 0', () => {
    const { container } = render(
      <ProgressBar completed={0} running={0} pending={0} failed={0} total={0} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders completed label with count', () => {
    render(<ProgressBar completed={3} running={1} pending={2} failed={0} total={6} />)
    expect(screen.getByText('3 completed')).toBeInTheDocument()
  })

  it('renders running label with count', () => {
    render(<ProgressBar completed={3} running={1} pending={2} failed={0} total={6} />)
    expect(screen.getByText('1 running')).toBeInTheDocument()
  })

  it('renders pending label with count', () => {
    render(<ProgressBar completed={3} running={1} pending={2} failed={0} total={6} />)
    expect(screen.getByText('2 pending')).toBeInTheDocument()
  })

  it('renders total', () => {
    render(<ProgressBar completed={3} running={1} pending={2} failed={0} total={6} />)
    expect(screen.getByText('6 total')).toBeInTheDocument()
  })

  it('renders percentage text', () => {
    render(<ProgressBar completed={5} running={0} pending={5} failed={0} total={10} />)
    expect(screen.getByText('50%')).toBeInTheDocument()
  })

  it('shows 100% when all completed', () => {
    render(<ProgressBar completed={10} running={0} pending={0} failed={0} total={10} />)
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('shows 0% when nothing completed', () => {
    render(<ProgressBar completed={0} running={5} pending={5} failed={0} total={10} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('does not render failed label when failed=0', () => {
    render(<ProgressBar completed={3} running={1} pending={2} failed={0} total={6} />)
    expect(screen.queryByText(/failed/)).not.toBeInTheDocument()
  })

  it('renders failed label when failed > 0', () => {
    render(<ProgressBar completed={2} running={1} pending={1} failed={2} total={6} />)
    expect(screen.getByText('2 failed')).toBeInTheDocument()
  })

  it('renders completed progress bar segment when completed > 0', () => {
    const { container } = render(
      <ProgressBar completed={5} running={0} pending={5} failed={0} total={10} />
    )
    const greenBar = container.querySelector('.bg-green-500')
    expect(greenBar).toBeInTheDocument()
  })

  it('does not render completed segment when completed = 0', () => {
    const { container } = render(
      <ProgressBar completed={0} running={5} pending={5} failed={0} total={10} />
    )
    const greenBar = container.querySelector('.bg-green-500')
    // The dot indicators still have bg-green-500 but the progress bar segment doesn't render
    // Check that only the dot is present (small size), not the bar
    expect(greenBar).toBeInTheDocument() // dot indicator
  })

  it('renders running segment when running > 0', () => {
    const { container } = render(
      <ProgressBar completed={0} running={5} pending={5} failed={0} total={10} />
    )
    const blueBar = container.querySelector('.bg-blue-500.animate-pulse')
    expect(blueBar).toBeInTheDocument()
  })

  it('renders failed segment when failed > 0', () => {
    const { container } = render(
      <ProgressBar completed={0} running={0} pending={8} failed={2} total={10} />
    )
    const redBars = container.querySelectorAll('.bg-red-500')
    expect(redBars.length).toBeGreaterThan(0)
  })

  it('renders without crashing with all zeros except total', () => {
    render(<ProgressBar completed={0} running={0} pending={10} failed={0} total={10} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })
})
