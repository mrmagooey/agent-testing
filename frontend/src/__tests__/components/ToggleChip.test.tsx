import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ToggleChip from '../../components/ToggleChip'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ToggleChip', () => {
  it('renders the label text', () => {
    render(<ToggleChip label="python" checked={false} onChange={vi.fn()} />)
    expect(screen.getByText('python')).toBeInTheDocument()
  })

  it('renders a checkbox input', () => {
    render(<ToggleChip label="python" checked={false} onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })

  it('checkbox is checked when checked=true', () => {
    render(<ToggleChip label="python" checked={true} onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('checkbox is not checked when checked=false', () => {
    render(<ToggleChip label="python" checked={false} onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('calls onChange with true when unchecked checkbox is clicked', () => {
    const onChange = vi.fn()
    render(<ToggleChip label="python" checked={false} onChange={onChange} />)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('calls onChange with false when checked checkbox is clicked', () => {
    const onChange = vi.fn()
    render(<ToggleChip label="python" checked={true} onChange={onChange} />)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('is disabled when disabled=true', () => {
    render(<ToggleChip label="python" checked={false} onChange={vi.fn()} disabled={true} />)
    expect(screen.getByRole('checkbox')).toBeDisabled()
  })

  it('is not disabled by default', () => {
    render(<ToggleChip label="python" checked={false} onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox')).not.toBeDisabled()
  })

  it('does not call onChange when disabled and change event fired', () => {
    const onChange = vi.fn()
    render(<ToggleChip label="python" checked={false} onChange={onChange} disabled={true} />)
    // jsdom respects disabled on fireEvent.change (not click) — the input is marked disabled
    fireEvent.change(screen.getByRole('checkbox'), { target: { checked: true } })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('renders value as data-value attribute on label', () => {
    render(<ToggleChip label="python" checked={false} onChange={vi.fn()} value="py" />)
    // The wrapping label element has data-value
    const label = screen.getByText('python').closest('label')
    expect(label).toHaveAttribute('data-value', 'py')
  })

  it('renders without value prop without crashing', () => {
    render(<ToggleChip label="go" checked={false} onChange={vi.fn()} />)
    expect(screen.getByText('go')).toBeInTheDocument()
  })

  it('active chip applies active class styles', () => {
    render(<ToggleChip label="active" checked={true} onChange={vi.fn()} />)
    const label = screen.getByText('active').closest('label')!
    expect(label.className).toContain('bg-amber-600')
  })

  it('inactive chip does not apply active class styles', () => {
    render(<ToggleChip label="inactive" checked={false} onChange={vi.fn()} />)
    const label = screen.getByText('inactive').closest('label')!
    expect(label.className).not.toContain('bg-amber-600')
  })
})
