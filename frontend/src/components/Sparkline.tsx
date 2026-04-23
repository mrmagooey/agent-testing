import { useNavigate } from 'react-router-dom'
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  ReferenceDot,
} from 'recharts'
import type { TrendPoint } from '../api/client'

interface SparklineProps {
  points: TrendPoint[]
  /** Called when the user clicks a data point. Parent handles navigation. */
  onPointClick?: (experimentId: string) => void
}

interface TooltipPayloadEntry {
  payload?: TrendPoint
}

function SparklineTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: TooltipPayloadEntry[]
}) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p) return null
  const shortId = p.experiment_id.slice(0, 8)
  const date = p.completed_at ? p.completed_at.slice(0, 10) : '—'
  return (
    <div
      className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 shadow-lg"
      style={{ pointerEvents: 'none' }}
    >
      <div className="font-mono">{shortId}…</div>
      <div className="text-gray-400">{date}</div>
      <div>
        F1{' '}
        <span className="font-semibold text-amber-300">{p.f1.toFixed(3)}</span>
      </div>
    </div>
  )
}

export default function Sparkline({ points, onPointClick }: SparklineProps) {
  const navigate = useNavigate()

  if (points.length === 0) {
    return (
      <span className="text-gray-400 dark:text-gray-500 text-xs italic">
        no data
      </span>
    )
  }

  const lastPoint = points[points.length - 1]

  const handleClick = (data: { activePayload?: Array<{ payload: TrendPoint }> }) => {
    const clicked = data?.activePayload?.[0]?.payload
    if (!clicked) return
    if (onPointClick) {
      onPointClick(clicked.experiment_id)
    } else {
      navigate(`/experiments/${clicked.experiment_id}`)
    }
  }

  return (
    <ResponsiveContainer width={120} height={32}>
      <LineChart
        data={points}
        margin={{ top: 2, right: 2, bottom: 2, left: 2 }}
        onClick={handleClick}
        style={{ cursor: 'pointer' }}
        aria-label="F1 score sparkline"
        role="img"
      >
        <title>F1 score over time</title>
        <Tooltip
          content={<SparklineTooltip />}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="f1"
          stroke="#f59e0b"
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
        {/* Highlight the last data point */}
        <ReferenceDot
          x={lastPoint.experiment_id}
          y={lastPoint.f1}
          r={3}
          fill="#f59e0b"
          stroke="#fff"
          strokeWidth={1}
          xAxisId={0}
          yAxisId={0}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
