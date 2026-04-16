import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listBatches, runSmokeTest, type Batch } from '../api/client'

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

export default function Dashboard() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState<Batch[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [smokeTest, setSmokeTest] = useState<SmokeTestState>({ status: 'idle' })

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

  useEffect(() => {
    listBatches()
      .then(setBatches)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const active = batches.filter((b) => b.status === 'running' || b.status === 'pending')
  const completed = batches
    .filter((b) => b.status === 'completed')
    .sort((a, b) => new Date(b.completed_at ?? b.created_at).getTime() - new Date(a.completed_at ?? a.created_at).getTime())
    .slice(0, 10)

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
        <h2 className="text-lg font-semibold mb-4">Active Batches</h2>
        {active.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">No active batches.</p>
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
          <p className="text-sm text-gray-400 dark:text-gray-500">No completed batches yet.</p>
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
