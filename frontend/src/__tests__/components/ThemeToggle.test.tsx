import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ThemeToggle from '../../components/ThemeToggle'

// Mock the useTheme hook
vi.mock('../../hooks/useTheme', () => ({
  useTheme: vi.fn(),
}))

import { useTheme } from '../../hooks/useTheme'
const mockUseTheme = vi.mocked(useTheme)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ThemeToggle', () => {
  it('renders the LIGHT and DARK labels', () => {
    mockUseTheme.mockReturnValue({ isDark: false, toggle: vi.fn() })
    render(<ThemeToggle />)
    expect(screen.getByText('[ LIGHT ]')).toBeInTheDocument()
    expect(screen.getByText('[ DARK ]')).toBeInTheDocument()
  })

  it('renders a button element', () => {
    mockUseTheme.mockReturnValue({ isDark: false, toggle: vi.fn() })
    render(<ThemeToggle />)
    expect(screen.getByRole('button')).toBeInTheDocument()
  })

  it('shows "Switch to dark mode" aria-label when in light mode', () => {
    mockUseTheme.mockReturnValue({ isDark: false, toggle: vi.fn() })
    render(<ThemeToggle />)
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toBeInTheDocument()
  })

  it('shows "Switch to light mode" aria-label when in dark mode', () => {
    mockUseTheme.mockReturnValue({ isDark: true, toggle: vi.fn() })
    render(<ThemeToggle />)
    expect(screen.getByRole('button', { name: 'Switch to light mode' })).toBeInTheDocument()
  })

  it('calls toggle when button is clicked', () => {
    const toggle = vi.fn()
    mockUseTheme.mockReturnValue({ isDark: false, toggle })
    render(<ThemeToggle />)
    fireEvent.click(screen.getByRole('button'))
    expect(toggle).toHaveBeenCalledTimes(1)
  })

  it('calls toggle when clicked in dark mode', () => {
    const toggle = vi.fn()
    mockUseTheme.mockReturnValue({ isDark: true, toggle })
    render(<ThemeToggle />)
    fireEvent.click(screen.getByRole('button'))
    expect(toggle).toHaveBeenCalledTimes(1)
  })

  it('has correct title in light mode', () => {
    mockUseTheme.mockReturnValue({ isDark: false, toggle: vi.fn() })
    render(<ThemeToggle />)
    expect(screen.getByTitle('Switch to dark mode')).toBeInTheDocument()
  })

  it('has correct title in dark mode', () => {
    mockUseTheme.mockReturnValue({ isDark: true, toggle: vi.fn() })
    render(<ThemeToggle />)
    expect(screen.getByTitle('Switch to light mode')).toBeInTheDocument()
  })
})
