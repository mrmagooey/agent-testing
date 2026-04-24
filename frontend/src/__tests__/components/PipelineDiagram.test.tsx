import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PipelineDiagram from '../../components/PipelineDiagram'

function renderWithRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('PipelineDiagram', () => {
  it('renders all 5 stage labels in order', () => {
    renderWithRouter(<PipelineDiagram />)

    const labels = ['Configure', 'Expand Matrix', 'Schedule', 'Execute', 'Aggregate & Report']
    labels.forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument()
    })
  })

  it('renders stages in the correct visual order', () => {
    const { container } = renderWithRouter(<PipelineDiagram />)

    // All stage cards are rendered with data-stage-index attributes
    const stageEls = container.querySelectorAll('[data-stage-index]')
    expect(stageEls).toHaveLength(5)

    const indices = Array.from(stageEls).map((el) =>
      Number(el.getAttribute('data-stage-index')),
    )
    expect(indices).toEqual([0, 1, 2, 3, 4])
  })

  it('the Configure step links to /experiments/new', () => {
    renderWithRouter(<PipelineDiagram />)

    // The Configure node should render as an anchor pointing to /experiments/new
    const configureLink = screen.getByRole('link', { name: /configure/i })
    expect(configureLink).toBeInTheDocument()
    expect(configureLink).toHaveAttribute('href', '/experiments/new')
  })

  it('the other four stages are not links', () => {
    renderWithRouter(<PipelineDiagram />)

    // There should be exactly one link in the diagram (Configure)
    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(1)
  })

  it('renders the section header text', () => {
    renderWithRouter(<PipelineDiagram />)
    expect(screen.getByText(/experiment pipeline/i)).toBeInTheDocument()
  })

  it('renders the subheader about empty state', () => {
    renderWithRouter(<PipelineDiagram />)
    expect(
      screen.getByText(/no experiments running/i),
    ).toBeInTheDocument()
  })

  it('renders stage descriptions', () => {
    renderWithRouter(<PipelineDiagram />)
    expect(screen.getByText(/pick models, strategies, dimensions/i)).toBeInTheDocument()
    expect(screen.getByText(/cartesian product/i)).toBeInTheDocument()
    expect(screen.getByText(/queue k8s jobs/i)).toBeInTheDocument()
    expect(screen.getByText(/workers review code/i)).toBeInTheDocument()
    expect(screen.getByText(/findings indexed/i)).toBeInTheDocument()
  })
})
