import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { useTheme } from '../hooks/useTheme'

export interface DimensionChartProps {
  data: Array<Record<string, unknown>>
  xKey: string
  yKey: string
  title: string
  color?: string
  isRatio?: boolean
}

export default function DimensionChart({
  data,
  xKey,
  yKey,
  title,
  color,
  isRatio,
}: DimensionChartProps) {
  const { isDark } = useTheme()
  const barColor = color ?? (isDark ? '#F0B84E' : '#F5A524')

  const values = data.map((d) => d[yKey] as number).filter((v) => typeof v === 'number' && !isNaN(v))
  const maxVal = values.length > 0 ? Math.max(...values) : 1

  const domainMax = isRatio === false
    ? Math.ceil(maxVal * 1.1 * 10) / 10
    : Math.min(1, Math.ceil(maxVal * 1.1 * 10) / 10) || 1

  return (
    <div className="bg-card rounded-sm p-4 border border-border">
      <h3 className="font-mono text-[11px] tracking-[0.2em] uppercase text-muted-foreground mb-4">{title}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" opacity={1} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={{ fontSize: 10, fill: 'var(--muted-foreground)', fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'var(--muted-foreground)', fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={false}
            tickLine={false}
            domain={[0, domainMax]}
            tickFormatter={(v) => (typeof v === 'number' ? v.toFixed(1) : v)}
          />
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
          <Bar dataKey={yKey} fill={barColor} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
