import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ExperimentImport from '../../pages/ExperimentImport'
import type { ImportSummary } from '../../api/client'

// ─── XHR mock helpers ────────────────────────────────────────────────────────

interface MockXHR {
  open: ReturnType<typeof vi.fn>
  send: ReturnType<typeof vi.fn>
  setRequestHeader: ReturnType<typeof vi.fn>
  upload: { addEventListener: ReturnType<typeof vi.fn> }
  addEventListener: ReturnType<typeof vi.fn>
  status: number
  responseText: string
  // Convenience handles for tests to trigger events
  _triggerLoad: () => void
  _triggerError: () => void
  _triggerProgress: (loaded: number, total: number) => void
}

function makeMockXHR(): MockXHR {
  const uploadListeners: Record<string, ((e: ProgressEvent) => void)[]> = {}
  const listeners: Record<string, (() => void)[]> = {}

  const xhr: MockXHR = {
    open: vi.fn(),
    send: vi.fn(),
    setRequestHeader: vi.fn(),
    status: 200,
    responseText: '{}',
    upload: {
      addEventListener: vi.fn((event: string, cb: (e: ProgressEvent) => void) => {
        uploadListeners[event] = uploadListeners[event] ?? []
        uploadListeners[event].push(cb)
      }),
    },
    addEventListener: vi.fn((event: string, cb: () => void) => {
      listeners[event] = listeners[event] ?? []
      listeners[event].push(cb)
    }),
    _triggerLoad() {
      for (const cb of listeners['load'] ?? []) cb()
    },
    _triggerError() {
      for (const cb of listeners['error'] ?? []) cb()
    },
    _triggerProgress(loaded: number, total: number) {
      const event = { lengthComputable: true, loaded, total } as ProgressEvent
      for (const cb of uploadListeners['progress'] ?? []) cb(event)
    },
  }
  return xhr
}

// ─── Render helper ───────────────────────────────────────────────────────────

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/experiments/import']}>
      <ExperimentImport />
    </MemoryRouter>,
  )
}

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('ExperimentImport — initial state', () => {
  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByRole('heading', { name: /import experiment/i })).toBeInTheDocument()
  })

  it('keeps the upload button disabled when no file is selected', () => {
    renderPage()
    expect(screen.getByRole('button', { name: /upload/i })).toBeDisabled()
  })

  it('renders conflict policy radio group with reject selected by default', () => {
    renderPage()
    const rejectRadio = screen.getByRole('radio', { name: /reject/i })
    expect(rejectRadio).toBeChecked()
    expect(screen.getByRole('radio', { name: /rename/i })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: /merge/i })).not.toBeChecked()
  })
})

describe('ExperimentImport — file selection via drop', () => {
  it('enables the upload button after a file is dropped', () => {
    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    const file = new File(['content'], 'bundle.secrev.zip', { type: 'application/zip' })

    fireEvent.drop(dropzone, {
      dataTransfer: { files: [file] },
    })

    expect(screen.getByRole('button', { name: /upload/i })).not.toBeDisabled()
  })

  it('displays the dropped file name', () => {
    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    const file = new File(['content'], 'my-experiment.secrev.zip', { type: 'application/zip' })

    fireEvent.drop(dropzone, {
      dataTransfer: { files: [file] },
    })

    expect(screen.getByText('my-experiment.secrev.zip')).toBeInTheDocument()
  })
})

describe('ExperimentImport — upload progress', () => {
  it('calls onProgress and updates the progress display', async () => {
    let capturedXhr: MockXHR | null = null
    const mockXhr = makeMockXHR()
    mockXhr.status = 200

    const successSummary: ImportSummary = {
      experiment_id: 'exp-imported-1',
      renamed_from: null,
      runs_imported: 5,
      runs_skipped: 0,
      datasets_missing: [],
      warnings: [],
      findings_indexed: 42,
    }
    mockXhr.responseText = JSON.stringify(successSummary)

    vi.spyOn(globalThis, 'XMLHttpRequest').mockImplementation(() => {
      capturedXhr = mockXhr
      return mockXhr as unknown as XMLHttpRequest
    })

    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    const file = new File(['content'], 'bundle.secrev.zip', { type: 'application/zip' })
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } })

    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    // Trigger progress at 50%
    await act(async () => {
      capturedXhr!._triggerProgress(50, 100)
    })

    expect(screen.getByRole('progressbar')).toBeInTheDocument()

    // Trigger completion
    await act(async () => {
      capturedXhr!._triggerLoad()
    })

    await waitFor(() => {
      expect(screen.getByText('exp-imported-1')).toBeInTheDocument()
    })
  })
})

describe('ExperimentImport — success path', () => {
  it('renders summary card with experiment_id link and counts', async () => {
    const mockXhr = makeMockXHR()
    mockXhr.status = 200
    const summary: ImportSummary = {
      experiment_id: 'exp-abc-123',
      renamed_from: null,
      runs_imported: 7,
      runs_skipped: 2,
      datasets_missing: [],
      warnings: [],
      findings_indexed: 88,
    }
    mockXhr.responseText = JSON.stringify(summary)
    let capturedXhr: MockXHR | null = null

    vi.spyOn(globalThis, 'XMLHttpRequest').mockImplementation(() => {
      capturedXhr = mockXhr
      return mockXhr as unknown as XMLHttpRequest
    })

    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    const file = new File(['data'], 'test.secrev.zip', { type: 'application/zip' })
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } })

    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await act(async () => {
      capturedXhr!._triggerLoad()
    })

    await waitFor(() => {
      expect(screen.getByText('Import successful')).toBeInTheDocument()
    })

    // Experiment ID is a link to the detail page
    const link = screen.getByRole('link', { name: 'exp-abc-123' })
    expect(link).toHaveAttribute('href', '/experiments/exp-abc-123')

    expect(screen.getByText(/runs imported/i)).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText(/findings indexed/i)).toBeInTheDocument()
    expect(screen.getByText('88')).toBeInTheDocument()
  })

  it('shows renamed_from notice when present', async () => {
    const mockXhr = makeMockXHR()
    mockXhr.status = 200
    const summary: ImportSummary = {
      experiment_id: 'exp-abc-renamed',
      renamed_from: 'exp-original',
      runs_imported: 3,
      runs_skipped: 0,
      datasets_missing: [],
      warnings: [],
      findings_indexed: 10,
    }
    mockXhr.responseText = JSON.stringify(summary)
    let capturedXhr: MockXHR | null = null

    vi.spyOn(globalThis, 'XMLHttpRequest').mockImplementation(() => {
      capturedXhr = mockXhr
      return mockXhr as unknown as XMLHttpRequest
    })

    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [new File(['data'], 'test.secrev.zip', { type: 'application/zip' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await act(async () => {
      capturedXhr!._triggerLoad()
    })

    await waitFor(() => {
      expect(screen.getByText(/renamed from/i)).toBeInTheDocument()
      expect(screen.getByText('exp-original')).toBeInTheDocument()
    })
  })

  it('renders dataset-missing chips when datasets_missing is non-empty', async () => {
    const mockXhr = makeMockXHR()
    mockXhr.status = 200
    const summary: ImportSummary = {
      experiment_id: 'exp-missing-ds',
      renamed_from: null,
      runs_imported: 2,
      runs_skipped: 0,
      datasets_missing: ['cve-2024-001', 'cve-2024-002'],
      warnings: [],
      findings_indexed: 5,
    }
    mockXhr.responseText = JSON.stringify(summary)
    let capturedXhr: MockXHR | null = null

    vi.spyOn(globalThis, 'XMLHttpRequest').mockImplementation(() => {
      capturedXhr = mockXhr
      return mockXhr as unknown as XMLHttpRequest
    })

    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [new File(['data'], 'test.secrev.zip', { type: 'application/zip' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await act(async () => {
      capturedXhr!._triggerLoad()
    })

    await waitFor(() => {
      expect(screen.getByText('cve-2024-001')).toBeInTheDocument()
      expect(screen.getByText('cve-2024-002')).toBeInTheDocument()
    })
  })
})

describe('ExperimentImport — error path', () => {
  it('shows red error banner on 409 conflict response', async () => {
    const mockXhr = makeMockXHR()
    mockXhr.status = 409
    mockXhr.responseText = JSON.stringify({ detail: 'Experiment already exists with this ID.' })
    let capturedXhr: MockXHR | null = null

    vi.spyOn(globalThis, 'XMLHttpRequest').mockImplementation(() => {
      capturedXhr = mockXhr
      return mockXhr as unknown as XMLHttpRequest
    })

    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [new File(['data'], 'test.secrev.zip', { type: 'application/zip' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await act(async () => {
      capturedXhr!._triggerLoad()
    })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByRole('alert')).toHaveTextContent('Experiment already exists with this ID.')
    })
  })

  it('shows error banner with generic message on 400 response', async () => {
    const mockXhr = makeMockXHR()
    mockXhr.status = 400
    mockXhr.responseText = JSON.stringify({ detail: 'Schema mismatch in bundle.' })
    let capturedXhr: MockXHR | null = null

    vi.spyOn(globalThis, 'XMLHttpRequest').mockImplementation(() => {
      capturedXhr = mockXhr
      return mockXhr as unknown as XMLHttpRequest
    })

    renderPage()

    const dropzone = screen.getByRole('button', { name: /drop zone/i })
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [new File(['data'], 'test.secrev.zip', { type: 'application/zip' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await act(async () => {
      capturedXhr!._triggerLoad()
    })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Schema mismatch in bundle.')
    })
  })
})
