import { Link } from 'react-router-dom'
import {
  SlidersHorizontal,
  LayoutGrid,
  ListOrdered,
  Cpu,
  BarChart3,
  ArrowRight,
  ArrowDown,
} from 'lucide-react'
import { cn } from '@/lib/utils'

interface Stage {
  key: string
  label: string
  description: string
  icon: React.ReactNode
  link?: string
}

const STAGES: Stage[] = [
  {
    key: 'configure',
    label: 'Configure',
    description: 'Pick models, strategies, dimensions',
    icon: <SlidersHorizontal size={22} />,
    link: '/experiments/new',
  },
  {
    key: 'expand',
    label: 'Expand Matrix',
    description: 'Cartesian product → N runs',
    icon: <LayoutGrid size={22} />,
  },
  {
    key: 'schedule',
    label: 'Schedule',
    description: 'Queue K8s Jobs respecting concurrency caps',
    icon: <ListOrdered size={22} />,
  },
  {
    key: 'execute',
    label: 'Execute',
    description: 'Workers review code in parallel',
    icon: <Cpu size={22} />,
  },
  {
    key: 'aggregate',
    label: 'Aggregate & Report',
    description: 'Findings indexed, matrix report rendered',
    icon: <BarChart3 size={22} />,
  },
]

function StageCard({ stage, index }: { stage: Stage; index: number }) {
  const inner = (
    <div
      className={cn(
        'flex flex-col items-center gap-2 rounded-sm border border-border bg-card px-4 py-4 text-center',
        'transition-colors duration-150',
        stage.link
          ? 'cursor-pointer hover:border-primary/60 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
          : 'cursor-default hover:bg-muted/40',
      )}
    >
      <div className="text-muted-foreground">{stage.icon}</div>
      <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.15em] text-foreground">
        {stage.label}
      </p>
      <p className="font-mono text-[10px] text-muted-foreground leading-snug max-w-[120px]">
        {stage.description}
      </p>
    </div>
  )

  if (stage.link) {
    return (
      <Link
        to={stage.link}
        aria-label={`${stage.label}: ${stage.description}`}
        data-stage-index={index}
        className="focus-visible:outline-none focus-visible:ring-0"
      >
        {inner}
      </Link>
    )
  }

  return (
    <div
      aria-label={`${stage.label}: ${stage.description}`}
      data-stage-index={index}
    >
      {inner}
    </div>
  )
}

export default function PipelineDiagram() {
  return (
    <div className="rounded-sm border border-border bg-card p-6">
      {/* Header */}
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted-foreground mb-1">
        // Experiment pipeline
      </p>
      <p className="font-mono text-xs text-muted-foreground mb-6">
        No experiments running — here&apos;s what happens when you start one.
      </p>

      {/* Desktop: horizontal row; Mobile: vertical stack */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        {STAGES.map((stage, i) => (
          <div
            key={stage.key}
            className="flex flex-col md:flex-row md:items-center gap-3 flex-1"
          >
            <div className="flex-1">
              <StageCard stage={stage} index={i} />
            </div>
            {i < STAGES.length - 1 && (
              <>
                {/* Arrow between stages: right on desktop, down on mobile */}
                <div className="flex items-center justify-center text-muted-foreground/50 md:flex-shrink-0">
                  <ArrowDown size={16} className="md:hidden" />
                  <ArrowRight size={16} className="hidden md:block" />
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
