import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import DiffViewer from '../../components/DiffViewer'

// Mock CodeViewer since it uses CodeMirror which doesn't render in jsdom
vi.mock('../../components/CodeViewer', () => ({
  default: ({ content, language }: { content: string; language?: string }) => (
    <div data-testid="code-viewer" data-language={language}>
      {content}
    </div>
  ),
}))

// Also mock useTheme used by CodeViewer
vi.mock('../../hooks/useTheme', () => ({
  useTheme: () => ({ isDark: false, toggle: vi.fn() }),
}))

describe('DiffViewer', () => {
  it('renders without crashing', () => {
    const { container } = render(<DiffViewer before="old code" after="new code" />)
    expect(container.firstChild).toBeInTheDocument()
  })

  it('renders "Before" label', () => {
    render(<DiffViewer before="old" after="new" />)
    expect(screen.getByText('Before')).toBeInTheDocument()
  })

  it('renders "After" label', () => {
    render(<DiffViewer before="old" after="new" />)
    expect(screen.getByText('After')).toBeInTheDocument()
  })

  it('passes before content to first CodeViewer', () => {
    render(<DiffViewer before="original content" after="updated content" />)
    const viewers = screen.getAllByTestId('code-viewer')
    expect(viewers[0].textContent).toBe('original content')
  })

  it('passes after content to second CodeViewer', () => {
    render(<DiffViewer before="original content" after="updated content" />)
    const viewers = screen.getAllByTestId('code-viewer')
    expect(viewers[1].textContent).toBe('updated content')
  })

  it('passes language prop to both CodeViewers', () => {
    render(<DiffViewer before="old" after="new" language="python" />)
    const viewers = screen.getAllByTestId('code-viewer')
    expect(viewers[0]).toHaveAttribute('data-language', 'python')
    expect(viewers[1]).toHaveAttribute('data-language', 'python')
  })

  it('renders two CodeViewer instances', () => {
    render(<DiffViewer before="a" after="b" />)
    expect(screen.getAllByTestId('code-viewer')).toHaveLength(2)
  })

  it('renders without language prop', () => {
    render(<DiffViewer before="before text" after="after text" />)
    const viewers = screen.getAllByTestId('code-viewer')
    expect(viewers).toHaveLength(2)
  })

  it('handles empty strings for before and after', () => {
    render(<DiffViewer before="" after="" />)
    expect(screen.getByText('Before')).toBeInTheDocument()
    expect(screen.getByText('After')).toBeInTheDocument()
  })
})
