import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import {
  getFileTree,
  getLabels,
  getFileContent,
  listTemplates,
  previewInjection,
  injectVuln,
  type Label,
  type FileTree as FileTreeData,
  type InjectionTemplate,
} from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import FileTree from '../components/FileTree'
import CodeViewer from '../components/CodeViewer'
import DiffViewer from '../components/DiffViewer'

type InjectStep = 1 | 2 | 3 | 4 | 5

export default function DatasetDetail() {
  const { name: datasetName } = useParams<{ name: string }>()
  const [tree, setTree] = useState<FileTreeData>({})
  const [labels, setLabels] = useState<Label[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<{ content: string; language: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Injection workflow
  const [injectStep, setInjectStep] = useState<InjectStep | null>(null)
  const [templates, setTemplates] = useState<InjectionTemplate[]>([])
  const [selectedTemplate, setSelectedTemplate] = useState<InjectionTemplate | null>(null)
  const [injectFile, setInjectFile] = useState<string | null>(null)
  const [substitutions, setSubstitutions] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<{ before: string; after: string; language: string } | null>(null)
  const [injecting, setInjecting] = useState(false)
  const [injectSuccess, setInjectSuccess] = useState<string | null>(null)

  useEffect(() => {
    if (!datasetName) return
    Promise.all([getFileTree(datasetName), getLabels(datasetName)])
      .then(([t, l]) => {
        setTree(t)
        setLabels(l)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [datasetName])

  useEffect(() => {
    if (!selectedFile || !datasetName) return
    getFileContent(datasetName, selectedFile)
      .then(setFileContent)
      .catch(() => null)
  }, [selectedFile, datasetName])

  const labelCounts = labels.reduce(
    (acc, l) => ({ ...acc, [l.file_path]: (acc[l.file_path] ?? 0) + 1 }),
    {} as Record<string, number>
  )

  const loadTemplates = async () => {
    if (templates.length > 0) return
    const t = await listTemplates()
    setTemplates(t)
  }

  const startInject = async () => {
    await loadTemplates()
    setInjectStep(1)
  }

  const handleTemplateSelect = (t: InjectionTemplate) => {
    setSelectedTemplate(t)
    const matches = t.description.match(/\{\{(\w+)\}\}/g) ?? []
    const placeholders = matches.map((m) => m.slice(2, -2))
    const initial = Object.fromEntries(placeholders.map((p) => [p, '']))
    setSubstitutions(initial)
    setInjectStep(2)
  }

  const handleInjectFileSelect = (path: string) => {
    setInjectFile(path)
    setInjectStep(3)
  }

  const handlePreview = async () => {
    if (!datasetName || !selectedTemplate || !injectFile) return
    try {
      const result = await previewInjection(datasetName, selectedTemplate.template_id, injectFile, substitutions)
      setPreview(result)
      setInjectStep(4)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed')
    }
  }

  const handleInject = async () => {
    if (!datasetName || !selectedTemplate || !injectFile) return
    setInjecting(true)
    try {
      const result = await injectVuln(datasetName, selectedTemplate.template_id, injectFile, substitutions)
      setInjectSuccess(result.label_id)
      setInjectStep(5)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Injection failed')
    } finally {
      setInjecting(false)
    }
  }

  const hasUnsavedSubstitutions = Object.values(substitutions).some((v) => v.trim() !== '')

  const handleCloseModal = () => {
    if (hasUnsavedSubstitutions && injectStep !== null && injectStep < 5) {
      const confirmed = window.confirm('Discard unsaved changes?')
      if (!confirmed) return
    }
    setInjectStep(null)
    setPreview(null)
    setInjectSuccess(null)
    setSubstitutions({})
    setSelectedTemplate(null)
    setInjectFile(null)
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading dataset...</div>
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Datasets', to: '/datasets' }, { label: datasetName ?? '' }]} />

      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold font-mono text-gray-900 dark:text-gray-100">{datasetName}</h1>
        <button
          onClick={startInject}
          className="px-4 py-2 bg-orange-600 hover:bg-orange-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Inject Vulnerability
        </button>
      </div>

      {/* Two-panel layout */}
      <div className="grid lg:grid-cols-3 gap-4">
        {/* File tree */}
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 overflow-auto max-h-[600px]">
          <h2 className="font-semibold text-sm mb-3">Files</h2>
          <FileTree
            tree={tree}
            onSelect={setSelectedFile}
            labelCounts={labelCounts}
            selectedPath={selectedFile ?? undefined}
          />
        </div>

        {/* File viewer */}
        <div className="lg:col-span-2 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          {selectedFile ? (
            <>
              <p className="text-xs font-mono text-gray-500 dark:text-gray-400 mb-3">{selectedFile}</p>
              {fileContent ? (
                <CodeViewer
                  content={fileContent.content}
                  language={fileContent.language}
                  maxHeight="550px"
                />
              ) : (
                <div className="flex items-center justify-center h-48 text-gray-400">Loading file...</div>
              )}
            </>
          ) : (
            <div className="flex items-center justify-center h-48 text-gray-400">
              Select a file to view
            </div>
          )}
        </div>
      </div>

      {/* Labels table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-4">Labels ({labels.length})</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-gray-500 dark:text-gray-400">
              <tr>
                <th className="text-left pb-2">File</th>
                <th className="text-left pb-2">Lines</th>
                <th className="text-left pb-2">Vuln Class</th>
                <th className="text-left pb-2">Severity</th>
                <th className="text-left pb-2">CWE</th>
                <th className="text-left pb-2">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {labels.map((l) => (
                <tr
                  key={l.label_id}
                  onClick={() => setSelectedFile(l.file_path)}
                  className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                >
                  <td className="py-2 font-mono text-xs text-indigo-600 dark:text-indigo-400 max-w-xs truncate">
                    {l.file_path}
                  </td>
                  <td className="py-2 font-mono text-xs text-gray-500">
                    {l.line_start}–{l.line_end}
                  </td>
                  <td className="py-2 font-mono text-xs">{l.vuln_class}</td>
                  <td className="py-2 text-xs">{l.severity}</td>
                  <td className="py-2 font-mono text-xs text-gray-500">{l.cwe ?? '—'}</td>
                  <td className="py-2 text-xs text-gray-500">{l.source}</td>
                </tr>
              ))}
              {labels.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-gray-400">No labels yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Injection workflow modal */}
      {injectStep !== null && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white dark:bg-gray-800 rounded-xl w-full max-w-3xl max-h-[90vh] overflow-y-auto shadow-2xl">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
              <h2 className="font-semibold">
                Inject Vulnerability — Step {injectStep}/5
              </h2>
              <button
                onClick={handleCloseModal}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl"
              >
                ×
              </button>
            </div>

            <div className="p-6">
              {injectStep === 1 && (
                <div className="space-y-3">
                  <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">Select a vulnerability template:</p>
                  <div className="space-y-2 max-h-80 overflow-y-auto">
                    {templates.map((t) => (
                      <button
                        key={t.template_id}
                        onClick={() => handleTemplateSelect(t)}
                        className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-indigo-50 dark:hover:bg-indigo-950 transition-colors"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-sm">{t.vuln_class}</span>
                          <div className="flex gap-2">
                            <span className="text-xs font-mono text-gray-500">{t.language}</span>
                            <span className="text-xs font-mono text-gray-500">{t.cwe}</span>
                            <span className="text-xs text-orange-600">{t.severity}</span>
                          </div>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">{t.description}</p>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {injectStep === 2 && (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    Select a target file (filtered to {selectedTemplate?.language}):
                  </p>
                  <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 max-h-80 overflow-y-auto">
                    <FileTree
                      tree={tree}
                      onSelect={handleInjectFileSelect}
                      selectedPath={injectFile ?? undefined}
                    />
                  </div>
                </div>
              )}

              {injectStep === 3 && (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    Fill in substitutions for <code className="font-mono">{selectedTemplate?.template_id}</code>:
                  </p>
                  {Object.keys(substitutions).length === 0 ? (
                    <p className="text-sm text-gray-400">No substitutions required.</p>
                  ) : (
                    Object.entries(substitutions).map(([key, val]) => (
                      <div key={key}>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 block mb-1">
                          {'{{'}{key}{'}}'}
                        </label>
                        <input
                          type="text"
                          value={val}
                          onChange={(e) => setSubstitutions((s) => ({ ...s, [key]: e.target.value }))}
                          className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 font-mono"
                        />
                      </div>
                    ))
                  )}
                  <button
                    onClick={handlePreview}
                    className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium transition-colors"
                  >
                    Preview Injection
                  </button>
                </div>
              )}

              {injectStep === 4 && preview && (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">Review the diff before confirming:</p>
                  <DiffViewer before={preview.before} after={preview.after} language={preview.language} />
                  <button
                    onClick={handleInject}
                    disabled={injecting}
                    className="px-4 py-2 rounded-lg bg-orange-600 hover:bg-orange-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    {injecting ? 'Injecting…' : 'Confirm & Inject'}
                  </button>
                </div>
              )}

              {injectStep === 5 && (
                <div className="text-center py-8">
                  <div className="text-4xl mb-4">✓</div>
                  <p className="font-semibold text-green-600 dark:text-green-400">Injection successful!</p>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
                    New label ID: <code className="font-mono">{injectSuccess}</code>
                  </p>
                  <button
                    onClick={() => { setInjectStep(null); setPreview(null); setInjectSuccess(null) }}
                    className="mt-4 px-4 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-sm"
                  >
                    Close
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
