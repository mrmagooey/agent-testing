import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listBatches, runSmokeTest, type Batch } from '../api/client'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

const POLL_INTERVAL_MS = 15_000

function formatElapsed(createdAt: string): string {
  const ms = Date.now() - new Date(createdAt).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function formatLastUpdated(ms: number): string {
  const s = Math.floor((Date.now() - ms) / 1000)
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  return `${m}m ago`
}

function SimpleProgressBar({ completed, total }: { completed: number; total: number }) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 rounded-full bg-gray-200 dark:bg-gray-700">
        <div
          className="h-full rounded-full bg-indigo-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-500 w-12 text-right">{completed}/{total}</span>
    </div>
  )
}

const STATUS_BADGE: Record<Batch['status'], string> = {
  pending: 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400',
  running: 'bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300',
  completed: 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300',
  failed: 'bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300',
  cancelled: 'bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300',
}

type SmokeTestState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'success'; batch_id: string; message: string }
  | { status: 'error'; message: string }

function SparklineChart({ data, dataKey, color, label }: {
  data: Array<Record<string, unknown>>
  dataKey: string
  color: string
  label: string
}) {
  if (data.length < 2) return null
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
      <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">{label}</p>
      <ResponsiveContainer width="100%" height={64}>
        <LineChart data={data} margin={{ top: 2, right: 4, left: 0, bottom: 2 }}>
          <XAxis dataKey="label" hide />
          <YAxis hide />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1f2937',
              border: '1px solid #374151',
              borderRadius: 4,
              fontSize: 11,
            }}
            labelStyle={{ color: '#f9fafb' }}
            itemStyle={{ color: '#d1d5db' }}
          />
          <Line
            type="monotone"
            dataKey={dataKey}
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function PollingIndicator({ polling, lastUpdated }: { polling: boolean; lastUpdated: number | null }) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 5000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="flex items-center gap-2 text-xs text-gray-400 dark:text-gray-500">
      {polling ? (
        <span className="inline-block h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
      ) : (
        <span className="inline-block h-2 w-2 rounded-full bg-gray-300 dark:bg-gray-600" />
      )}
      {lastUpdated !== null && (
        <span>{formatLastUpdated(lastUpdated)}</span>
      )}
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState<Batch[]>([])
  const [loading, setLoading] = useState(true)
  const [polling, setPolling] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [smokeTest, setSmokeTest] = useState<SmokeTestState>({ status: 'idle' })
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function handleSmokeTest() {
    if (smokeTest.status === 'running') return
    setSmokeTest({ status: 'running' })
    try {
      const result = await runSmokeTest()
      setSmokeTest({ status: 'success', batch_id: result.batch_id, message: result.message })
    } catch (e) {
      setSmokeTest({ status: 'error', message: (e as Error).message })
    }
  }

  const fetchBatches = useCallback(async (isInitial = false) => {
    if (!isInitial) setPolling(true)
    try {
      const data = await listBatches()
      setBatches(data)
      setLastUpdated(Date.now())
    } catch (e) {
      if (isInitial) setError((e as Error).message)
    } finally {
      if (isInitial) setLoading(false)
      setPolling(false)
    }
  }, [])

  useEffect(() => {
    fetchBatches(true)
    pollingRef.current = setInterval(() => fetchBatches(false), POLL_INTERVAL_MS)
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [fetchBatches])

  const active = batches.filter((b) => b.status === 'running' || b.status === 'pending')
  const completed = batches
    .filter((b) => b.status === 'completed')
    .sort((a, b) => new Date(b.completed_at ?? b.created_at).getTime() - new Date(a.completed_at ?? a.created_at).getTime())
    .slice(0, 10)

  const costSparkData = completed.slice().reverse().map((b, i) => ({
    label: `#${i + 1}`,
    cost: b.total_cost_usd,
  }))

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Dashboard</h1>
        <button
          onClick={() => navigate('/batches/new')}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          New Batch
        </button>
      </div>

      {/* Smoke Test */}
      <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Smoke Test</h2>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              Runs a single-model, single-strategy batch against the first available dataset.
              Capped at $5.00.
            </p>
          </div>
          <button
            onClick={handleSmokeTest}
            disabled={smokeTest.status === 'running'}
            className="shrink-0 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 disabled:cursor-not-allowed text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
          >
            {smokeTest.status === 'running' && (
              <span className="inline-block h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
            )}
            {smokeTest.status === 'running' ? 'Running…' : 'Run Smoke Test'}
          </button>
        </div>

        {smokeTest.status === 'success' && (
          <div className="mt-4 rounded-lg bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 px-4 py-3 text-sm text-green-800 dark:text-green-300 flex items-start gap-2">
            <span className="mt-0.5 text-green-500">&#10003;</span>
            <span>
              {smokeTest.message}{' '}
              <Link
                to={`/batches/${smokeTest.batch_id}`}
                className="underline font-medium hover:text-green-900 dark:hover:text-green-100"
              >
                View batch
              </Link>
            </span>
          </div>
        )}

        {smokeTest.status === 'error' && (
          <div className="mt-4 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-300">
            {smokeTest.message}
          </div>
        )}
      </section>

      {/* Active Batches */}
      <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Active Batches</h2>
          <PollingIndicator polling={polling} lastUpdated={lastUpdated} />
        </div>
        {active.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500">
            <svg className="w-10 h-10 mb-3 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p className="text-sm font-medium">No active batches.</p>
            <p className="text-xs mt-1">Start a new batch to see it here.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr>
                  <th className="text-left pb-2">Batch ID</th>
                  <th className="text-left pb-2">Dataset</th>
                  <th className="text-left pb-2 w-48">Progress</th>
                  <th className="text-left pb-2">Cost</th>
                  <th className="text-left pb-2">Status</th>
                  <th className="text-left pb-2">Elapsed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {active.map((b) => (
                  <tr
                    key={b.batch_id}
                    onClick={() => navigate(`/batches/${b.batch_id}`)}
                    className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                  >
                    <td className="py-2 font-mono text-xs text-indigo-600 dark:text-indigo-400">
                      {b.batch_id.slice(0, 8)}…
                    </td>
                    <td className="py-2 text-gray-600 dark:text-gray-400">{b.dataset}</td>
                    <td className="py-2 w-48">
                      <SimpleProgressBar completed={b.completed_runs} total={b.total_runs} />
                    </td>
                    <td className="py-2 text-gray-700 dark:text-gray-300">
                      ${b.total_cost_usd.toFixed(2)}
                    </td>
                    <td className="py-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_BADGE[b.status]}`}>
                        {b.status}
                      </span>
                    </td>
                    <td className="py-2 text-gray-500 dark:text-gray-400 font-mono text-xs">
                      {formatElapsed(b.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Recent Completed */}
      <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="text-lg font-semibold mb-4">Recent Batches</h2>
        {completed.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500">
            <svg className="w-10 h-10 mb-3 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
            <p className="text-sm font-medium">No completed batches yet.</p>
            <p className="text-xs mt-1">Completed batches will appear here.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr>
                  <th className="text-left pb-2">Batch ID</th>
                  <th className="text-left pb-2">Dataset</th>
                  <th className="text-left pb-2">Completed</th>
                  <th className="text-left pb-2">Runs</th>
                  <th className="text-left pb-2">Total Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {completed.map((b) => (
                  <tr
                    key={b.batch_id}
                    onClick={() => navigate(`/batches/${b.batch_id}`)}
                    className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                  >
                    <td className="py-2 font-mono text-xs text-indigo-600 dark:text-indigo-400">
                      {b.batch_id.slice(0, 8)}…
                    </td>
                    <td className="py-2 text-gray-600 dark:text-gray-400">{b.dataset}</td>
                    <td className="py-2 text-gray-500 dark:text-gray-400 text-xs">
                      {b.completed_at ? formatDate(b.completed_at) : '—'}
                    </td>
                    <td className="py-2 text-gray-700 dark:text-gray-300">{b.total_runs}</td>
                    <td className="py-2 text-gray-700 dark:text-gray-300">
                      ${b.total_cost_usd.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Sparklines */}
      {costSparkData.length >= 2 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-3">Trends</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <SparklineChart
              data={costSparkData}
              dataKey="cost"
              color="#6366f1"
              label="Cost per batch (USD)"
            />
          </div>
        </section>
      )}

      {/* System Health */}
      <section className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="text-lg font-semibold mb-4">System Health</h2>
        <p className="text-sm text-gray-400 dark:text-gray-500">
          Coordinator status, active jobs, and storage metrics — coming soon.
        </p>
      </section>
    </div>
  )
}
