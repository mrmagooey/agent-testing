import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listBatches, runSmokeTest, type Batch } from '../api/client'
import AccuracyHeatmap from '../components/AccuracyHeatmap'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import EmptyState from '../components/EmptyState'
import { PageLoadingSpinner } from '../components/Skeleton'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

const POLL_INTERVAL_MS = 15_000
const DEFAULT_SPEND_CAP = 50 // USD — shown if batch has no explicit cap

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

const STATUS_BADGE_VARIANT: Record<Batch['status'], 'default' | 'secondary' | 'destructive' | 'outline'> = {
  pending: 'secondary',
  running: 'outline',
  completed: 'default',
  failed: 'destructive',
  cancelled: 'outline',
}

// Keep legacy classes for running/cancelled which need custom colors
const STATUS_BADGE_CLS: Record<Batch['status'], string> = {
  pending: '',
  running: 'border-blue-400 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/30',
  completed: 'bg-green-600 dark:bg-green-700 text-white border-transparent',
  failed: '',
  cancelled: 'border-yellow-400 text-yellow-700 dark:text-yellow-300 bg-yellow-50 dark:bg-yellow-900/30',
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
    <div>
      <p className="text-xs font-medium text-gray-500 dark:text-gray-300 mb-2">{label}</p>
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
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 5000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="flex items-center gap-2 text-xs text-gray-400 dark:text-gray-300">
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

// Cost Headroom card — logic preserved, wrapped in shadcn Card
function CostHeadroomCard({ batches }: { batches: Batch[] }) {
  const completedBatches = batches.filter((b) => b.status === 'completed')
  if (completedBatches.length === 0) return null

  const totalSpend = completedBatches.reduce((sum, b) => sum + b.total_cost_usd, 0)
  const cap = completedBatches[0]?.spend_cap_usd ?? DEFAULT_SPEND_CAP
  const pct = Math.min((totalSpend / cap) * 100, 100)

  const isWarn = pct >= 80 && pct < 95
  const isCrit = pct >= 95

  const barColor = isCrit ? 'bg-red-500' : isWarn ? 'bg-yellow-500' : 'bg-emerald-500'
  const alertCls = isCrit
    ? 'text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800'
    : isWarn
    ? 'text-yellow-700 dark:text-yellow-300 bg-yellow-50 dark:bg-yellow-950 border border-yellow-200 dark:border-yellow-800'
    : ''

  return (
    <div>
      <p className="text-xs font-medium text-gray-500 dark:text-gray-300 mb-2">Cost Headroom</p>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-2xl font-bold text-gray-900 dark:text-gray-100">${totalSpend.toFixed(2)}</span>
        <span className="text-sm text-gray-400 dark:text-gray-300">/ ${cap.toFixed(0)} cap</span>
        <span className="ml-auto text-sm font-semibold text-gray-700 dark:text-gray-200">{pct.toFixed(0)}%</span>
      </div>
      <div className="h-2 rounded-full bg-gray-200 dark:bg-gray-700 mb-3">
        <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      {(isWarn || isCrit) && (
        <p className={`text-xs px-2 py-1 rounded ${alertCls}`}>
          {isCrit ? '⛔ Critical: spending near cap — consider pausing new batches.' : '⚠ Warning: 80% of spend cap reached.'}
        </p>
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

  if (loading) return <PageLoadingSpinner />

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
        <Button
          onClick={() => navigate('/batches/new')}
          className="bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          New Batch
        </Button>
      </div>

      {/* Smoke Test */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <CardTitle>Smoke Test</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Runs a single-model, single-strategy batch against the first available dataset.
                Capped at $5.00.
              </p>
            </div>
            <Button
              onClick={handleSmokeTest}
              disabled={smokeTest.status === 'running'}
              className="shrink-0 bg-indigo-600 hover:bg-indigo-700 text-white"
            >
              {smokeTest.status === 'running' && (
                <span className="inline-block h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
              )}
              {smokeTest.status === 'running' ? 'Running…' : 'Run Smoke Test'}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {smokeTest.status === 'success' && (
            <div className="rounded-lg bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 px-4 py-3 text-sm text-green-800 dark:text-green-300 flex items-start gap-2">
              <span className="mt-0.5 text-green-500">&#10003;</span>
              <span>
                {smokeTest.message}{' '}
                <Link
                  to={`/batches/${smokeTest.batch_id}`}
                  className="underline font-medium hover:text-green-900 dark:hover:text-green-100 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none rounded"
                >
                  View batch
                </Link>
              </span>
            </div>
          )}
          {smokeTest.status === 'error' && (
            <div className="rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-300">
              {smokeTest.message}
            </div>
          )}
          {smokeTest.status === 'idle' && <span className="sr-only">Ready</span>}
        </CardContent>
      </Card>

      {/* Active Batches */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Active Batches</CardTitle>
            <PollingIndicator polling={polling} lastUpdated={lastUpdated} />
          </div>
        </CardHeader>
        <CardContent>
          {active.length === 0 ? (
            <EmptyState
              icon={
                <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              }
              title="No active batches."
              subtitle="Start a new batch to see it here."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-gray-500 dark:text-gray-300">
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
                      <td className="py-2 text-gray-600 dark:text-gray-300">{b.dataset}</td>
                      <td className="py-2 w-48">
                        <SimpleProgressBar completed={b.completed_runs} total={b.total_runs} />
                      </td>
                      <td className="py-2 text-gray-700 dark:text-gray-200">
                        ${b.total_cost_usd.toFixed(2)}
                      </td>
                      <td className="py-2">
                        <Badge
                          variant={STATUS_BADGE_VARIANT[b.status]}
                          className={STATUS_BADGE_CLS[b.status]}
                        >
                          {b.status}
                        </Badge>
                      </td>
                      <td className="py-2 text-gray-500 dark:text-gray-300 font-mono text-xs">
                        {formatElapsed(b.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent Completed */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Batches</CardTitle>
        </CardHeader>
        <CardContent>
          {completed.length === 0 ? (
            <EmptyState
              title="No completed batches yet."
              subtitle="Completed batches will appear here."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-gray-500 dark:text-gray-300">
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
                      <td className="py-2 text-gray-600 dark:text-gray-300">{b.dataset}</td>
                      <td className="py-2 text-gray-500 dark:text-gray-300 text-xs">
                        {b.completed_at ? formatDate(b.completed_at) : '—'}
                      </td>
                      <td className="py-2 text-gray-700 dark:text-gray-200">{b.total_runs}</td>
                      <td className="py-2 text-gray-700 dark:text-gray-200">
                        ${b.total_cost_usd.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Trends section — cost headroom + sparklines */}
      {costSparkData.length >= 2 && (
        <Card>
          <CardHeader>
            <CardTitle>Trends</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <SparklineChart
                data={costSparkData}
                dataKey="cost"
                color="#6366f1"
                label="Cost per batch (USD)"
              />
              <CostHeadroomCard batches={batches} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Accuracy Heatmap */}
      <Card>
        <CardHeader>
          <CardTitle>Model × Strategy Accuracy</CardTitle>
        </CardHeader>
        <CardContent>
          <AccuracyHeatmap />
        </CardContent>
      </Card>

      {/* System Health */}
      <Card>
        <CardHeader>
          <CardTitle>System Health</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Coordinator status, active jobs, and storage metrics — coming soon.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
