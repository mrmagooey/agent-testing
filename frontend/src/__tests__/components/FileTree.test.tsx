import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FileTree from '../../components/FileTree'

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('FileTree', () => {
  it('renders file names', () => {
    render(<FileTree tree={{ 'views.py': null, 'utils.py': null }} onSelect={vi.fn()} />)
    expect(screen.getByText('views.py')).toBeInTheDocument()
    expect(screen.getByText('utils.py')).toBeInTheDocument()
  })

  it('renders directory names', () => {
    render(
      <FileTree
        tree={{ myapp: { 'views.py': null } }}
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByText('myapp')).toBeInTheDocument()
  })

  it('calls onSelect with correct path when file is clicked', () => {
    const onSelect = vi.fn()
    render(<FileTree tree={{ 'views.py': null }} onSelect={onSelect} />)

    fireEvent.click(screen.getByText('views.py'))
    expect(onSelect).toHaveBeenCalledWith('views.py')
  })

  it('calls onSelect with nested path for nested file', () => {
    const onSelect = vi.fn()
    render(
      <FileTree
        tree={{ myapp: { 'views.py': null } }}
        onSelect={onSelect}
      />
    )

    // Directory is expanded at depth=0 by default
    fireEvent.click(screen.getByText('views.py'))
    expect(onSelect).toHaveBeenCalledWith('myapp/views.py')
  })

  it('collapses and expands directory on click', () => {
    render(
      <FileTree
        tree={{ src: { 'main.py': null, 'util.py': null } }}
        onSelect={vi.fn()}
      />
    )

    // At depth=0, directory starts expanded — main.py and util.py are visible
    expect(screen.getByText('main.py')).toBeInTheDocument()

    // Click the directory to collapse
    fireEvent.click(screen.getByText('src'))
    expect(screen.queryByText('main.py')).not.toBeInTheDocument()

    // Click again to expand
    fireEvent.click(screen.getByText('src'))
    expect(screen.getByText('main.py')).toBeInTheDocument()
  })

  it('renders label count badge for files with labels', () => {
    render(
      <FileTree
        tree={{ 'views.py': null }}
        onSelect={vi.fn()}
        labelCounts={{ 'views.py': 3 }}
      />
    )
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('does not render label badge when count is 0', () => {
    render(
      <FileTree
        tree={{ 'views.py': null }}
        onSelect={vi.fn()}
        labelCounts={{ 'views.py': 0 }}
      />
    )
    expect(screen.queryByText('0')).not.toBeInTheDocument()
  })

  it('highlights the selected file path', () => {
    render(
      <FileTree
        tree={{ 'views.py': null, 'utils.py': null }}
        onSelect={vi.fn()}
        selectedPath="views.py"
      />
    )
    const selectedButton = screen.getByText('views.py').closest('button')!
    expect(selectedButton.className).toMatch(/blue/)
  })

  it('renders empty tree without crashing', () => {
    render(<FileTree tree={{}} onSelect={vi.fn()} />)
    // Should render without error
    expect(document.body).toBeDefined()
  })

  it('renders nested directories multiple levels deep', () => {
    render(
      <FileTree
        tree={{ src: { components: { 'App.tsx': null } } }}
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByText('src')).toBeInTheDocument()
    expect(screen.getByText('components')).toBeInTheDocument()
  })
})
