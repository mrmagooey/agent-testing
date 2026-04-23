import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import PageDescription from '../../components/PageDescription'

describe('PageDescription', () => {
  it('renders children text', () => {
    render(<PageDescription>This is a description</PageDescription>)
    expect(screen.getByText('This is a description')).toBeInTheDocument()
  })

  it('renders with data-testid attribute', () => {
    render(<PageDescription>Hello</PageDescription>)
    expect(screen.getByTestId('page-description')).toBeInTheDocument()
  })

  it('renders as a paragraph element', () => {
    render(<PageDescription>Content</PageDescription>)
    const el = screen.getByTestId('page-description')
    expect(el.tagName).toBe('P')
  })

  it('renders complex children including JSX', () => {
    render(
      <PageDescription>
        Visit <strong>the docs</strong> for more info
      </PageDescription>
    )
    expect(screen.getByTestId('page-description')).toBeInTheDocument()
    expect(screen.getByText('the docs')).toBeInTheDocument()
  })

  it('renders multiple text nodes', () => {
    render(
      <PageDescription>
        {'First part. '}
        {'Second part.'}
      </PageDescription>
    )
    const el = screen.getByTestId('page-description')
    expect(el.textContent).toContain('First part.')
    expect(el.textContent).toContain('Second part.')
  })

  it('renders without crashing with empty string child', () => {
    render(<PageDescription>{''}</PageDescription>)
    expect(screen.getByTestId('page-description')).toBeInTheDocument()
  })
})
