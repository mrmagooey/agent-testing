import { useState } from 'react'
import { discoverCVEs, resolveCVE, importCVE, type CVECandidate } from '../api/client'
import CVECandidateTable from '../components/CVECandidateTable'
import PageDescription from '../components/PageDescription'
import { chipClasses } from '../components/ToggleChip'
import ToggleChip from '../components/ToggleChip'

const LANGUAGES = ['python', 'javascript', 'java', 'go', 'rust', 'c', 'cpp']
const VULN_CLASSES = ['sqli', 'xss', 'rce', 'ssrf', 'path_traversal', 'auth_bypass', 'xxe', 'insecure_deser', 'buffer_overflow']
const SEVERITIES = ['critical', 'high', 'medium', 'low']

export default function CVEDiscovery() {
  const [activeTab, setActiveTab] = useState<'search' | 'resolve'>('search')

  // Search tab state
  const [languages, setLanguages] = useState<string[]>([])
  const [vulnClasses, setVulnClasses] = useState<string[]>([])
  const [severities, setSeverities] = useState<string[]>([])
  const [patchSizeMin, setPatchSizeMin] = useState('')
  const [patchSizeMax, setPatchSizeMax] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [searching, setSearching] = useState(false)
  const [candidates, setCandidates] = useState<CVECandidate[]>([])
  const [searchError, setSearchError] = useState<string | null>(null)
  const [hasSearched, setHasSearched] = useState(false)

  // Resolve tab state
  const [cveId, setCveId] = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolved, setResolved] = useState<CVECandidate | null>(null)
  const [resolveError, setResolveError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [importSuccess, setImportSuccess] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)

  const toggleItem = (list: string[], setter: (v: string[]) => void, item: string) => {
    setter(list.includes(item) ? list.filter((i) => i !== item) : [...list, item])
  }

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    setSearching(true)
    setSearchError(null)
    setImportError(null)
    setHasSearched(true)
    try {
      const criteria: Record<string, unknown> = {}
      if (languages.length) criteria.languages = languages
      if (vulnClasses.length) criteria.vuln_classes = vulnClasses
      if (severities.length) criteria.severities = severities
      if (patchSizeMin) criteria.patch_size_min = parseInt(patchSizeMin, 10)
      if (patchSizeMax) criteria.patch_size_max = parseInt(patchSizeMax, 10)
      if (dateFrom) criteria.date_from = dateFrom
      if (dateTo) criteria.date_to = dateTo
      const result = await discoverCVEs(criteria)
      setCandidates(result)
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  const handleResolve = async () => {
    if (!cveId.trim()) return
    setResolving(true)
    setResolveError(null)
    setResolved(null)
    setImportSuccess(false)
    setImportError(null)
    try {
      const result = await resolveCVE(cveId.trim())
      setResolved(result)
    } catch (err) {
      setResolveError(err instanceof Error ? err.message : 'Resolution failed')
    } finally {
      setResolving(false)
    }
  }

  const handleImport = async (cveIds: string[]) => {
    setImporting(true)
    setImportError(null)
    try {
      await Promise.all(cveIds.map((id) => importCVE(id)))
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const handleImportResolved = async () => {
    if (!resolved) return
    setImporting(true)
    setImportError(null)
    try {
      await importCVE(resolved.cve_id)
      setImportSuccess(true)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const tabs = [
    { key: 'search' as const, label: 'Search' },
    { key: 'resolve' as const, label: 'Resolve CVE' },
  ]

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">CVE Discovery</h1>
      <PageDescription>
        Search public CVE feeds for real-world vulnerabilities by language, class, and severity, or resolve a specific CVE ID directly.
        Import any candidate as a new dataset to evaluate models against actual production bugs rather than synthetic ones.
      </PageDescription>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => { setActiveTab(t.key); setImportError(null) }}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              activeTab === t.key
                ? 'border-amber-600 text-amber-600 dark:text-amber-400'
                : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === 'search' && (
        <div className="space-y-6">
          {/* Criteria form */}
          <form onSubmit={handleSearch} className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-5">
            <div>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Languages</p>
              <div className="flex flex-wrap items-center gap-2">
                <AnyButton active={languages.length === 0} onClick={() => setLanguages([])} />
                {LANGUAGES.map((l) => (
                  <ToggleChip
                    key={l}
                    label={l}
                    value={l}
                    checked={languages.includes(l)}
                    onChange={() => toggleItem(languages, setLanguages, l)}
                  />
                ))}
              </div>
            </div>

            <div>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Vuln Classes</p>
              <div className="flex flex-wrap items-center gap-2">
                <AnyButton active={vulnClasses.length === 0} onClick={() => setVulnClasses([])} />
                {VULN_CLASSES.map((v) => (
                  <ToggleChip
                    key={v}
                    label={v}
                    value={v}
                    checked={vulnClasses.includes(v)}
                    onChange={() => toggleItem(vulnClasses, setVulnClasses, v)}
                  />
                ))}
              </div>
            </div>

            <div>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Severity</p>
              <div className="flex flex-wrap items-center gap-2">
                <AnyButton active={severities.length === 0} onClick={() => setSeverities([])} />
                {SEVERITIES.map((s) => (
                  <ToggleChip
                    key={s}
                    label={s}
                    value={s}
                    checked={severities.includes(s)}
                    onChange={() => toggleItem(severities, setSeverities, s)}
                  />
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <label className="text-xs text-gray-500 dark:text-gray-400 block mb-1">Patch size min (lines)</label>
                <input
                  type="number"
                  min={0}
                  value={patchSizeMin}
                  onChange={(e) => setPatchSizeMin(e.target.value)}
                  className="w-full text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 dark:text-gray-400 block mb-1">Patch size max (lines)</label>
                <input
                  type="number"
                  min={1}
                  value={patchSizeMax}
                  onChange={(e) => setPatchSizeMax(e.target.value)}
                  className="w-full text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 dark:text-gray-400 block mb-1">Date from</label>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="w-full text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 dark:text-gray-400 block mb-1">Date to</label>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="w-full text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={searching}
              className="px-6 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
            >
              {searching ? 'Searching…' : 'Search CVEs'}
            </button>
          </form>

          {searchError && (
            <div role="alert" className="p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
              {searchError}
            </div>
          )}

          {importError && (
            <div role="alert" className="p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
              {importError}
            </div>
          )}

          {candidates.length > 0 ? (
            <CVECandidateTable candidates={candidates} onImport={handleImport} />
          ) : hasSearched && !searching && !searchError ? (
            <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-400">
              No candidates matched. Most public advisories are rejected because they lack a resolvable GitHub fix commit. Try a specific CVE on the Resolve tab.
            </div>
          ) : null}
        </div>
      )}

      {activeTab === 'resolve' && (
        <div className="space-y-4">
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="font-semibold mb-4">Resolve CVE by ID</h2>
            <div className="flex gap-3">
              <input
                type="text"
                value={cveId}
                onChange={(e) => setCveId(e.target.value)}
                placeholder="CVE-2024-12345"
                className="flex-1 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 font-mono"
              />
              <button
                onClick={handleResolve}
                disabled={resolving || !cveId.trim()}
                className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
              >
                {resolving ? 'Resolving…' : 'Resolve'}
              </button>
            </div>

            {resolveError && (
              <p className="mt-3 text-sm text-red-600 dark:text-red-400">{resolveError}</p>
            )}

            {resolved && (
              <div className="mt-5 space-y-3">
                <dl className="grid grid-cols-2 gap-3 text-sm">
                  {[
                    ['CVE ID', resolved.cve_id],
                    ['Score', resolved.score.toFixed(2)],
                    ['Vuln Class', resolved.vuln_class],
                    ['Severity', resolved.severity],
                    ['Language', resolved.language],
                    ['Repo', resolved.repo],
                    ['Importable', resolved.importable ? 'Yes' : 'No'],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-gray-500 dark:text-gray-400">{k}</dt>
                      <dd className="font-medium font-mono text-xs">{v}</dd>
                    </div>
                  ))}
                </dl>
                {resolved.description && (
                  <p className="text-sm text-gray-600 dark:text-gray-400">{resolved.description}</p>
                )}
                {importError && (
                  <div role="alert" className="p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
                    {importError}
                  </div>
                )}
                {importSuccess ? (
                  <p className="text-sm text-green-600 dark:text-green-400 font-medium">Imported successfully.</p>
                ) : (
                  <button
                    onClick={handleImportResolved}
                    disabled={importing || !resolved.importable}
                    className="px-4 py-2 rounded-lg bg-green-600 hover:bg-green-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    {importing ? 'Importing…' : 'Import'}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function AnyButton({ active, onClick }: { active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active} className={chipClasses(active)}>
      <span className="font-mono">Any</span>
    </button>
  )
}
