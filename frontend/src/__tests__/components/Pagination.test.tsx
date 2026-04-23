import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Pagination from '../../components/Pagination'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Pagination', () => {
  it('shows "No results" when total is 0', () => {
    render(
      <Pagination total={0} limit={25} offset={0} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByText('No results')).toBeInTheDocument()
  })

  it('shows correct range text', () => {
    render(
      <Pagination total={100} limit={25} offset={0} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByText('Showing 1–25 of 100')).toBeInTheDocument()
  })

  it('shows correct range for second page', () => {
    render(
      <Pagination total={100} limit={25} offset={25} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByText('Showing 26–50 of 100')).toBeInTheDocument()
  })

  it('shows clamped "to" when last page is partial', () => {
    render(
      <Pagination total={30} limit={25} offset={25} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByText('Showing 26–30 of 30')).toBeInTheDocument()
  })

  it('prev button is disabled on first page', () => {
    render(
      <Pagination total={100} limit={25} offset={0} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
  })

  it('prev button is enabled on non-first page', () => {
    render(
      <Pagination total={100} limit={25} offset={25} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByRole('button', { name: 'Previous page' })).not.toBeDisabled()
  })

  it('next button is disabled on last page', () => {
    render(
      <Pagination total={25} limit={25} offset={0} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
  })

  it('next button is enabled when more pages exist', () => {
    render(
      <Pagination total={100} limit={25} offset={0} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    expect(screen.getByRole('button', { name: 'Next page' })).not.toBeDisabled()
  })

  it('calls onPageChange with next offset when Next clicked', () => {
    const onPageChange = vi.fn()
    render(
      <Pagination total={100} limit={25} offset={0} onPageChange={onPageChange} onLimitChange={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(onPageChange).toHaveBeenCalledWith(25)
  })

  it('calls onPageChange with previous offset when Prev clicked', () => {
    const onPageChange = vi.fn()
    render(
      <Pagination total={100} limit={25} offset={25} onPageChange={onPageChange} onLimitChange={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(onPageChange).toHaveBeenCalledWith(0)
  })

  it('prev page is clamped to 0 minimum', () => {
    const onPageChange = vi.fn()
    render(
      <Pagination total={100} limit={25} offset={10} onPageChange={onPageChange} onLimitChange={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    // offset - limit = 10 - 25 = -15, clamped to 0
    expect(onPageChange).toHaveBeenCalledWith(0)
  })

  it('renders default page size options', () => {
    render(
      <Pagination total={100} limit={25} offset={0} onPageChange={vi.fn()} onLimitChange={vi.fn()} />
    )
    const select = screen.getByRole('combobox')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(options).toContain('25')
    expect(options).toContain('50')
    expect(options).toContain('100')
  })

  it('renders custom pageSizes', () => {
    render(
      <Pagination
        total={100}
        limit={10}
        offset={0}
        onPageChange={vi.fn()}
        onLimitChange={vi.fn()}
        pageSizes={[10, 20, 30]}
      />
    )
    const select = screen.getByRole('combobox')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(options).toEqual(['10', '20', '30'])
  })

  it('calls onLimitChange and resets to page 0 when limit changes', () => {
    const onLimitChange = vi.fn()
    const onPageChange = vi.fn()
    render(
      <Pagination
        total={100}
        limit={25}
        offset={25}
        onPageChange={onPageChange}
        onLimitChange={onLimitChange}
      />
    )
    const select = screen.getByRole('combobox')
    fireEvent.change(select, { target: { value: '50' } })
    expect(onLimitChange).toHaveBeenCalledWith(50)
    expect(onPageChange).toHaveBeenCalledWith(0)
  })
})
