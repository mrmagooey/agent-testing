import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SkeletonLine, SkeletonCard, SkeletonTable, PageLoadingSpinner } from '../../components/Skeleton'

describe('SkeletonLine', () => {
  it('renders without crashing', () => {
    const { container } = render(<SkeletonLine />)
    expect(container.firstChild).toBeInTheDocument()
  })

  it('applies animate-pulse class', () => {
    const { container } = render(<SkeletonLine />)
    expect(container.firstChild).toHaveClass('animate-pulse')
  })

  it('applies custom className', () => {
    const { container } = render(<SkeletonLine className="w-1/2 h-6" />)
    const el = container.firstChild as HTMLElement
    expect(el.className).toContain('w-1/2')
    expect(el.className).toContain('h-6')
  })

  it('renders with no className prop (defaults to empty string)', () => {
    const { container } = render(<SkeletonLine />)
    expect(container.firstChild).toBeInTheDocument()
  })
})

describe('SkeletonCard', () => {
  it('renders default 3 rows', () => {
    const { container } = render(<SkeletonCard />)
    // Each row is a SkeletonLine div with animate-pulse
    const rows = container.querySelectorAll('.animate-pulse')
    expect(rows.length).toBe(3)
  })

  it('renders custom number of rows', () => {
    const { container } = render(<SkeletonCard rows={5} />)
    const rows = container.querySelectorAll('.animate-pulse')
    expect(rows.length).toBe(5)
  })

  it('renders 1 row', () => {
    const { container } = render(<SkeletonCard rows={1} />)
    const rows = container.querySelectorAll('.animate-pulse')
    expect(rows.length).toBe(1)
  })

  it('renders without crashing', () => {
    const { container } = render(<SkeletonCard />)
    expect(container.firstChild).toBeInTheDocument()
  })
})

describe('SkeletonTable', () => {
  it('renders default 5 rows x 4 cols', () => {
    const { container } = render(<SkeletonTable />)
    const wrapper = container.firstChild as HTMLElement
    // outer div has animate-pulse
    expect(wrapper).toHaveClass('animate-pulse')
    // 5 row divs inside
    const rowDivs = wrapper.querySelectorAll(':scope > div')
    expect(rowDivs.length).toBe(5)
  })

  it('renders custom rows and cols', () => {
    const { container } = render(<SkeletonTable rows={3} cols={2} />)
    const wrapper = container.firstChild as HTMLElement
    const rowDivs = wrapper.querySelectorAll(':scope > div')
    expect(rowDivs.length).toBe(3)
    // Each row has 2 col divs
    const firstRow = rowDivs[0]
    expect(firstRow.children.length).toBe(2)
  })

  it('has animate-pulse on outer container', () => {
    const { container } = render(<SkeletonTable />)
    expect(container.firstChild).toHaveClass('animate-pulse')
  })

  it('renders without crashing', () => {
    const { container } = render(<SkeletonTable />)
    expect(container.firstChild).toBeInTheDocument()
  })
})

describe('PageLoadingSpinner', () => {
  it('renders without crashing', () => {
    const { container } = render(<PageLoadingSpinner />)
    expect(container.firstChild).toBeInTheDocument()
  })

  it('renders Loading text', () => {
    const { container } = render(<PageLoadingSpinner />)
    expect(container.textContent).toContain('Loading')
  })

  it('renders a spinner element with animate-spin', () => {
    const { container } = render(<PageLoadingSpinner />)
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
  })
})
