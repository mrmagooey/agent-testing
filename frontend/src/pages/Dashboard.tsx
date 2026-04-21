import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listExperiments, runSmokeTest, type Experiment } from '../api/client'
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
import PageDescription from '../components/PageDescription'
import { PageLoadingSpinner } from '../components/Skeleton'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
} from '@/components/ui/card'

const POLL_INTERVAL_MS = 15_000
const DEFAULT_SPEND_CAP = 50 // USD — shown if experiment has no explicit cap

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
      <div className="flex-1 h-1.5 bg-muted rounded-none">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-muted-foreground font-mono tabular-nums w-14 text-right">{completed}/{total}</span>
    </div>
  )
}

const STATUS_SIGNAL: Record<Experiment['status'], string> = {
  pending: 'text-muted-foreground',
  running: 'text-signal-info',
  completed: 'text-signal-success',
  failed: 'text-signal-danger',
  cancelled: 'text-signal-warning',
}

type SmokeTestState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'success'; experiment_id: string; message: string }
  | { status: 'error'; message: string }

function SparklineChart({ data, dataKey, label }: {
  data: Array<Record<string, unknown>>
  dataKey: string
  label: string
}) {
  if (data.length < 2) return null
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-2">{label}</p>
      <ResponsiveContainer width="100%" height={64}>
        <LineChart data={data} margin={{ top: 2, right: 4, left: 0, bottom: 2 }}>
          <XAxis dataKey="label" hide />
          <YAxis hide />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--card)',
              border: '1px solid var(--border)',
              borderRadius: '0.25rem',
              fontSize: 11,
              fontFamily: 'JetBrains Mono, monospace',
            }}
            labelStyle={{ color: 'var(--card-foreground)' }}
            itemStyle={{ color: 'var(--muted-foreground)' }}
          />
          <Line
            type="monotone"
            dataKey={dataKey}
            stroke="#F5A524"
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
    <div className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
      {polling ? (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
      ) : (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
      )}
      {lastUpdated !== null && (
        <span className="tabular-nums">{formatLastUpdated(lastUpdated)}</span>
      )}
    </div>
  )
}

function CostHeadroomCard({ experiments }: { experiments: Experiment[] }) {
  const completedExperiments = experiments.filter((b) => b.status === 'completed')
  if (completedExperiments.length === 0) return null

  const totalSpend = completedExperiments.reduce((sum, b) => sum + b.total_cost_usd, 0)
  const cap = completedExperiments[0]?.spend_cap_usd ?? DEFAULT_SPEND_CAP
  const pct = Math.min((totalSpend / cap) * 100, 100)

  const isWarn = pct >= 80 && pct < 95
  const isCrit = pct >= 95

  const barColor = isCrit ? 'bg-signal-danger' : isWarn ? 'bg-signal-warning' : 'bg-signal-success'
  const alertCls = isCrit
    ? 'text-signal-danger border border-signal-danger/30'
    : isWarn
    ? 'text-signal-warning border border-signal-warning/30'
    : ''

  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-2">Cost Headroom</p>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="font-display text-2xl font-bold tabular-nums">${totalSpend.toFixed(2)}</span>
        <span className="text-xs text-muted-foreground font-mono tabular-nums">/ ${cap.toFixed(0)} cap</span>
        <span className="ml-auto text-sm font-mono tabular-nums">{pct.toFixed(0)}%</span>
      </div>
      <div className="h-1.5 bg-muted mb-3">
        <div className={`h-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      {(isWarn || isCrit) && (
        <p className={`text-xs font-mono px-2 py-1 ${alertCls}`}>
          {isCrit ? 'CRITICAL — spending near cap. Consider pausing new experiments.' : 'WARNING — 80% of spend cap reached.'}
        </p>
      )}
    </div>
  )
}

function SectionHeader({ label, count }: { label: string; count?: number }) {
  return (
    <h2 className="font-mono text-[11px] tracking-[0.2em] uppercase text-muted-foreground mb-3">
      // {label}{count !== undefined && count > 0 && (
        <span className="text-primary"> — {count}</span>
      )}
    </h2>
  )
}

const ROW_REVEAL_STYLE = (i: number): React.CSSProperties => ({
  animationName: 'row-reveal',
  animationDuration: '240ms',
  animationTimingFunction: 'ease-out',
  animationFillMode: 'both',
  animationDelay: `${Math.min(i, 7) * 40}ms`,
})

export default function Dashboard() {
  const navigate = useNavigate()
  const [experiments, setExperiments] = useState<Experiment[]>([])
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
      setSmokeTest({ status: 'success', experiment_id: result.experiment_id, message: result.message })
    } catch (e) {
      setSmokeTest({ status: 'error', message: (e as Error).message })
    }
  }

  const fetchExperiments = useCallback(async (isInitial = false) => {
    if (!isInitial) setPolling(true)
    try {
      const data = await listExperiments()
      setExperiments(data)
      setLastUpdated(Date.now())
    } catch (e) {
      if (isInitial) setError((e as Error).message)
    } finally {
      if (isInitial) setLoading(false)
      setPolling(false)
    }
  }, [])

  useEffect(() => {
    fetchExperiments(true)
    pollingRef.current = setInterval(() => fetchExperiments(false), POLL_INTERVAL_MS)
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [fetchExperiments])

  const active = experiments.filter((b) => b.status === 'running' || b.status === 'pending')
  const completed = experiments
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
      <div className="rounded-sm border border-signal-danger/40 bg-signal-danger/5 p-4 text-signal-danger font-mono text-sm">
        {error}
      </div>
    )
  }

  return (
    <>
      <style>{`
        @keyframes row-reveal {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: none; }
        }
      `}</style>
      <div className="space-y-10">
        {/* Page header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="font-display font-bold text-3xl tracking-tight">Dashboard</h1>
            <p className="mt-1 text-xs text-muted-foreground font-mono uppercase tracking-wider">// SYSTEM OVERVIEW</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={() => navigate('/compare')}
              variant="outline"
              className="font-mono text-xs uppercase tracking-wider"
            >
              Compare
            </Button>
            <Button
              onClick={() => navigate('/experiments/new')}
              className="bg-primary text-primary-foreground hover:bg-primary/90 font-mono text-xs uppercase tracking-wider"
            >
              New Experiment
            </Button>
          </div>
        </div>
        <PageDescription>
          Live view of active and recent experiments, total spend against cap, and model-vs-strategy accuracy across completed runs.
          Start a new experiment, trigger a smoke test, or click any experiment row to drill into its per-run results.
        </PageDescription>

        {/* Smoke Test */}
        <section>
          <SectionHeader label="Smoke Test" />
          <Card className="shadow-none rounded-sm border-border bg-card">
            <CardContent className="pt-6">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <p className="text-sm text-muted-foreground max-w-sm">
                  Runs a single-model, single-strategy experiment against the first available dataset.
                  Capped at{' '}
                  <span className="font-mono tabular-nums">$5.00</span>.
                </p>
                <Button
                  onClick={handleSmokeTest}
                  disabled={smokeTest.status === 'running'}
                  variant="outline"
                  className="shrink-0 font-mono text-xs uppercase tracking-wider rounded-sm"
                >
                  {smokeTest.status === 'running' && (
                    <span className="inline-block h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin mr-2" />
                  )}
                  {smokeTest.status === 'running' ? 'Running…' : 'Run Smoke Test'}
                </Button>
              </div>
              {smokeTest.status === 'success' && (
                <div className="mt-4 border border-signal-success/40 px-4 py-3 text-sm text-signal-success font-mono flex items-start gap-2">
                  <span className="mt-0.5">✓</span>
                  <span>
                    {smokeTest.message}{' '}
                    <Link
                      to={`/experiments/${smokeTest.experiment_id}`}
                      className="underline hover:text-signal-success/80 focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
                    >
                      View experiment →
                    </Link>
                  </span>
                </div>
              )}
              {smokeTest.status === 'error' && (
                <div className="mt-4 border border-signal-danger/40 px-4 py-3 text-sm text-signal-danger font-mono">
                  {smokeTest.message}
                </div>
              )}
            </CardContent>
          </Card>
        </section>

        <hr className="border-border" />

        {/* Active Experiments */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <SectionHeader label="Active Experiments" count={active.length} />
            <PollingIndicator polling={polling} lastUpdated={lastUpdated} />
          </div>
          {active.length === 0 ? (
            <EmptyState
              icon={
                <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              }
              title="No active experiments."
              subtitle="Start a new experiment to see it here."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Experiment ID</th>
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Dataset</th>
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground w-48">Progress</th>
                    <th className="text-right pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Cost</th>
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Status</th>
                    <th className="text-right pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Elapsed</th>
                  </tr>
                </thead>
                <tbody>
                  {active.map((b, i) => (
                    <tr
                      key={b.experiment_id}
                      onClick={() => navigate(`/experiments/${b.experiment_id}`)}
                      className="border-b border-border cursor-pointer hover:bg-muted/40 transition-colors"
                      style={ROW_REVEAL_STYLE(i)}
                    >
                      <td className="py-2.5 font-mono text-xs text-primary">
                        {b.experiment_id.slice(0, 8)}…
                      </td>
                      <td className="py-2.5 text-sm">{b.dataset}</td>
                      <td className="py-2.5 w-48">
                        <SimpleProgressBar completed={b.completed_runs} total={b.total_runs} />
                      </td>
                      <td className="py-2.5 text-right font-mono tabular-nums text-xs">
                        ${b.total_cost_usd.toFixed(2)}
                      </td>
                      <td className="py-2.5">
                        <span className={`font-mono text-[10px] uppercase tracking-wider border border-current px-1.5 py-0.5 ${STATUS_SIGNAL[b.status]}`}>
                          {b.status}
                        </span>
                      </td>
                      <td className="py-2.5 text-right font-mono tabular-nums text-xs text-muted-foreground">
                        {formatElapsed(b.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <hr className="border-border" />

        {/* Recent Completed */}
        <section>
          <SectionHeader label="Recent Experiments" count={completed.length} />
          {completed.length === 0 ? (
            <EmptyState
              title="No completed experiments yet."
              subtitle="Completed experiments will appear here."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Experiment ID</th>
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Dataset</th>
                    <th className="text-left pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Completed</th>
                    <th className="text-right pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Runs</th>
                    <th className="text-right pb-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Total Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {completed.map((b, i) => (
                    <tr
                      key={b.experiment_id}
                      onClick={() => navigate(`/experiments/${b.experiment_id}`)}
                      className="border-b border-border cursor-pointer hover:bg-muted/40 transition-colors"
                      style={ROW_REVEAL_STYLE(i)}
                    >
                      <td className="py-2.5 font-mono text-xs text-primary">
                        {b.experiment_id.slice(0, 8)}…
                      </td>
                      <td className="py-2.5 text-sm">{b.dataset}</td>
                      <td className="py-2.5 text-xs text-muted-foreground font-mono tabular-nums">
                        {b.completed_at ? formatDate(b.completed_at) : '—'}
                      </td>
                      <td className="py-2.5 text-right font-mono tabular-nums text-xs">{b.total_runs}</td>
                      <td className="py-2.5 text-right font-mono tabular-nums text-xs">
                        ${b.total_cost_usd.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <hr className="border-border" />

        {/* Trends */}
        {costSparkData.length >= 2 && (
          <section>
            <SectionHeader label="Trends" />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <SparklineChart
                data={costSparkData}
                dataKey="cost"
                label="Cost per experiment (USD)"
              />
              <CostHeadroomCard experiments={experiments} />
            </div>
            <hr className="border-border mt-10" />
          </section>
        )}

        {/* Accuracy Heatmap */}
        <section>
          <SectionHeader label="Model × Strategy Accuracy" />
          <AccuracyHeatmap />
        </section>

        <hr className="border-border" />

        {/* System Health */}
        <section>
          <SectionHeader label="System Health" />
          <p className="text-sm text-muted-foreground font-mono">
            Coordinator status, active jobs, and storage metrics — coming soon.
          </p>
        </section>
      </div>
    </>
  )
}
