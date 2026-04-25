import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ExportMenu from '../../components/ExportMenu'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  downloadReports: vi.fn((experimentId: string) => `/api/experiments/${experimentId}/results/download`),
  exportBundleUrl: vi.fn(
    (experimentId: string, datasetMode: 'reference' | 'descriptor' = 'descriptor') =>
      `/api/experiments/${experimentId}/export?dataset_mode=${datasetMode}`,
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

  it('opens the export dialog when "Export full bundle" is clicked', () => {
    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /export full bundle/i })).toBeInTheDocument()
  })

  it('defaults to "descriptor" mode in the dialog', () => {
    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    const descriptorRadio = screen.getByTestId('dataset-mode-radio-descriptor')
    expect(descriptorRadio).toBeChecked()
    const referenceRadio = screen.getByTestId('dataset-mode-radio-reference')
    expect(referenceRadio).not.toBeChecked()
  })

  it('allows selecting "reference" mode', () => {
    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    fireEvent.click(screen.getByTestId('dataset-mode-radio-reference'))

    expect(screen.getByTestId('dataset-mode-radio-reference')).toBeChecked()
    expect(screen.getByTestId('dataset-mode-radio-descriptor')).not.toBeChecked()
  })

  it('calls exportBundleUrl with descriptor mode by default and triggers download', () => {
    let capturedHref = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      capturedHref = this.href
    })

    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))
    fireEvent.click(screen.getByTestId('export-dialog-confirm'))

    expect(exportBundleUrl).toHaveBeenCalledWith('exp-99', 'descriptor')
    expect(capturedHref).toContain('exp-99')
    expect(capturedHref).toContain('export')
    clickSpy.mockRestore()
  })

  it('calls exportBundleUrl with reference mode when selected', () => {
    let capturedHref = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      capturedHref = this.href
    })

    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))
    fireEvent.click(screen.getByTestId('dataset-mode-radio-reference'))
    fireEvent.click(screen.getByTestId('export-dialog-confirm'))

    expect(exportBundleUrl).toHaveBeenCalledWith('exp-99', 'reference')
    expect(capturedHref).toContain('dataset_mode=reference')
    clickSpy.mockRestore()
  })

  it('uses descriptor mode URL which contains dataset_mode=descriptor', () => {
    let capturedHref = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      capturedHref = this.href
    })

    render(<ExportMenu experimentId="exp-99" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))
    fireEvent.click(screen.getByTestId('export-dialog-confirm'))

    expect(capturedHref).toContain('dataset_mode=descriptor')
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
    fireEvent.click(screen.getByTestId('export-dialog-confirm'))

    expect(capturedDownload).toBe('my-exp.secrev.zip')
    clickSpy.mockRestore()
  })

  it('closes the dialog when Cancel is clicked', () => {
    render(<ExportMenu experimentId="exp-1" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('export-dialog-cancel'))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows descriptor and reference mode descriptions', () => {
    render(<ExportMenu experimentId="exp-1" />)
    fireEvent.click(screen.getByTestId('export-bundle-btn'))

    expect(screen.getByText(/re-clone or re-derive/i)).toBeInTheDocument()
    expect(screen.getByText(/target must already have them/i)).toBeInTheDocument()
  })
})
