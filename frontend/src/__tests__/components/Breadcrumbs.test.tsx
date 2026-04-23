import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Breadcrumbs from '../../components/Breadcrumbs'
import type { BreadcrumbItem } from '../../components/Breadcrumbs'

function renderWithRouter(items: BreadcrumbItem[]) {
  return render(
    <MemoryRouter>
      <Breadcrumbs items={items} />
    </MemoryRouter>
  )
}

describe('Breadcrumbs', () => {
  it('renders without crashing with a single item', () => {
    renderWithRouter([{ label: 'Home' }])
    expect(screen.getByText('Home')).toBeInTheDocument()
  })

  it('renders nav with aria-label', () => {
    renderWithRouter([{ label: 'Home' }])
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()
  })

  it('renders multiple items', () => {
    renderWithRouter([
      { label: 'Home', to: '/' },
      { label: 'Experiments', to: '/experiments' },
      { label: 'Run 123' },
    ])
    expect(screen.getByText('Home')).toBeInTheDocument()
    expect(screen.getByText('Experiments')).toBeInTheDocument()
    expect(screen.getByText('Run 123')).toBeInTheDocument()
  })

  it('renders intermediate items with `to` as links', () => {
    renderWithRouter([
      { label: 'Home', to: '/' },
      { label: 'Experiments', to: '/experiments' },
      { label: 'Detail' },
    ])
    const homeLink = screen.getByRole('link', { name: 'Home' })
    expect(homeLink).toBeInTheDocument()
    // "Experiments" is not last but has to=, should also be a link
    const experimentsLink = screen.getByRole('link', { name: 'Experiments' })
    expect(experimentsLink).toBeInTheDocument()
  })

  it('renders last item as plain span, not a link', () => {
    renderWithRouter([
      { label: 'Home', to: '/' },
      { label: 'Detail' },
    ])
    // "Detail" is last so should not be a link even if `to` is omitted
    const links = screen.queryAllByRole('link')
    const linkTexts = links.map((l) => l.textContent)
    expect(linkTexts).not.toContain('Detail')
  })

  it('last item with `to` is still rendered as plain span (isLast wins)', () => {
    renderWithRouter([
      { label: 'Home', to: '/' },
      { label: 'Last', to: '/last' },
    ])
    // "Last" is the final item — should be a span, not a link
    const links = screen.queryAllByRole('link')
    const linkTexts = links.map((l) => l.textContent)
    expect(linkTexts).not.toContain('Last')
    expect(screen.getByText('Last')).toBeInTheDocument()
  })

  it('renders separator slashes between items', () => {
    renderWithRouter([
      { label: 'A', to: '/a' },
      { label: 'B', to: '/b' },
      { label: 'C' },
    ])
    const slashes = screen.getAllByText('/')
    expect(slashes).toHaveLength(2)
  })

  it('renders a single item with no separator', () => {
    renderWithRouter([{ label: 'Only' }])
    expect(screen.queryByText('/')).not.toBeInTheDocument()
  })

  it('renders empty items array without crashing', () => {
    renderWithRouter([])
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()
  })
})
