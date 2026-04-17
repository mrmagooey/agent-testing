import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import DownloadButton from '../../components/DownloadButton'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  downloadReports: vi.fn((batchId: string) => `/api/batches/${batchId}/results/download`),
}))

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('DownloadButton', () => {
  it('renders the default label text', () => {
    render(<DownloadButton batchId="b1" />)
    expect(screen.getByRole('button', { name: /download reports/i })).toBeInTheDocument()
  })

  it('renders a custom label when provided', () => {
    render(<DownloadButton batchId="b1" label="Export ZIP" />)
    expect(screen.getByRole('button', { name: /export zip/i })).toBeInTheDocument()
  })

  it('triggers a download when button is clicked (anchor.click is called)', () => {
    // Intercept anchor creation by tracking click calls via prototype spy
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<DownloadButton batchId="batch-42" />)
    fireEvent.click(screen.getByRole('button'))

    expect(clickSpy).toHaveBeenCalledOnce()
    clickSpy.mockRestore()
  })

  it('sets the correct href on the download anchor', () => {
    let capturedHref = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      capturedHref = this.href
    })

    render(<DownloadButton batchId="my-batch-123" />)
    fireEvent.click(screen.getByRole('button'))

    expect(capturedHref).toContain('my-batch-123')
    clickSpy.mockRestore()
  })

  it('sets the correct download filename', () => {
    let capturedDownload = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      capturedDownload = this.download
    })

    render(<DownloadButton batchId="my-batch-123" />)
    fireEvent.click(screen.getByRole('button'))

    expect(capturedDownload).toBe('batch-my-batch-123-reports.zip')
    clickSpy.mockRestore()
  })

  it('renders without crashing when no label prop is given', () => {
    render(<DownloadButton batchId="b1" />)
    expect(screen.getByRole('button')).toBeInTheDocument()
  })
})
