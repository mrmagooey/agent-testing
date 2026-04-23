import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import EmptyState from '../../components/EmptyState'

describe('EmptyState', () => {
  it('renders the title', () => {
    render(<EmptyState title="Nothing here yet" />)
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
  })

  it('renders without crashing with only a title', () => {
    const { container } = render(<EmptyState title="Empty" />)
    expect(container).toBeDefined()
  })

  it('renders subtitle when provided', () => {
    render(<EmptyState title="No results" subtitle="Try adjusting your filters" />)
    expect(screen.getByText('Try adjusting your filters')).toBeInTheDocument()
  })

  it('does not render subtitle when omitted', () => {
    render(<EmptyState title="No results" />)
    expect(screen.queryByText('Try adjusting your filters')).not.toBeInTheDocument()
  })

  it('renders a custom icon when provided', () => {
    render(
      <EmptyState
        title="Empty"
        icon={<span data-testid="custom-icon">icon</span>}
      />
    )
    expect(screen.getByTestId('custom-icon')).toBeInTheDocument()
  })

  it('renders default icon when no icon prop given', () => {
    const { container } = render(<EmptyState title="Empty" />)
    // Default icon is an SVG
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('renders both title and subtitle together', () => {
    render(<EmptyState title="No data" subtitle="Please add some data" />)
    expect(screen.getByText('No data')).toBeInTheDocument()
    expect(screen.getByText('Please add some data')).toBeInTheDocument()
  })

  it('renders empty string subtitle gracefully', () => {
    render(<EmptyState title="Title" subtitle="" />)
    // Empty string is falsy — subtitle paragraph should not render
    // The component uses `{subtitle && <p>...</p>}` so empty string means no <p> with empty text
    const { container } = render(<EmptyState title="Title2" subtitle="" />)
    // Only one <p> should exist (the title), not a second one for subtitle
    const paragraphs = container.querySelectorAll('p')
    expect(paragraphs).toHaveLength(1)
  })
})
