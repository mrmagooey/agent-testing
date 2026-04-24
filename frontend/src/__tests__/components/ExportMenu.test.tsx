import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ExportMenu from '../../components/ExportMenu'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  downloadReports: vi.fn((experimentId: string) => `/api/experiments/${experimentId}/results/download`),
  exportBundleUrl: vi.fn((experimentId: string, includeDatasets: boolean) =>
    `/api/experiments/${experimentId}/export?include_datasets=${includeDatasets}`,
  ),
}))

import { downloadReports, exportBundleUrl } from '../../api/client'

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('ExportMenu', () => {
  it('renders the disclosure toggle button', () => {
    render(<ExportMenu experimentId="exp-1" />)
    // The <summary> element acts as the disclosure toggle
    expect(screen.getByText(/download ▾/i)).toBeInTheDocument()
  })

  it('renders the "Download reports" item in the menu', () => {
    render(<ExportMenu experimentId="exp-1" />)
    expect(screen.getByRole('button', { name: /download reports/i })).toBeInTheDocument()
  })

  it('renders the "Export full bundle" item in the menu', () => {
    render(<ExportMenu experimentId="exp-1" />)
    expect(screen.getByRole('button', { name: /export full bundle/i })).toBeInTheDocument()
  })

  it('triggers a download with the reports URL when "Download reports" is clicked', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<ExportMenu experimentId="exp-42" />)
    fireEvent.click(screen.getByRole('button', { name: /download reports/i }))

    expect(downloadReports).toHaveBeenCalledWith('exp-42')
    expect(clickSpy).toHaveBeenCalledOnce()
    clickSpy.mockRestore()
  })

  it('triggers a download with the export bundle URL when "Export full bundle" is clicked', () => {
    let capturedHref = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      capturedHref = this.href
    })

    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    expect(exportBundleUrl).toHaveBeenCalledWith('exp-99', false)
    expect(capturedHref).toContain('exp-99')
    expect(capturedHref).toContain('export')
    clickSpy.mockRestore()
  })

  it('sets correct download filename for the export bundle', () => {
    let capturedDownload = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      capturedDownload = this.download
    })

    render(<ExportMenu experimentId="my-exp" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    expect(capturedDownload).toBe('my-exp.secrev.zip')
    clickSpy.mockRestore()
  })
})
