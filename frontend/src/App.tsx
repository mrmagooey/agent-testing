import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import ExperimentNew from './pages/ExperimentNew'
import ExperimentDetail from './pages/ExperimentDetail'
import RunDetail from './pages/RunDetail'
import RunCompare from './pages/RunCompare'
import CVEDiscovery from './pages/CVEDiscovery'
import Datasets from './pages/Datasets'
import DatasetDetail from './pages/DatasetDetail'
import DatasetSourceView from './pages/DatasetSourceView'
import Feedback from './pages/Feedback'
import Findings from './pages/Findings'
import Settings from './pages/Settings'
import ThemeToggle from './components/ThemeToggle'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/experiments/new', label: 'New Experiment', end: false },
  { to: '/compare', label: 'Compare', end: false },
  { to: '/datasets', label: 'Datasets', end: true },
  { to: '/datasets/discover', label: 'CVE Discovery', end: false },
  { to: '/findings', label: 'Findings', end: false },
  { to: '/feedback', label: 'Feedback', end: false },
  { to: '/settings', label: 'Settings', end: false },
]

function NavBar() {
  return (
    <>
      <style>{`
        .nav-cursor::before {
          content: '\\258A';
          display: inline-block;
          margin-right: 0.4em;
          color: var(--color-primary, #f59e0b);
        }
      `}</style>
      <nav className="bg-background/80 backdrop-blur-sm border-b border-border sticky top-0 z-50 h-12">
        <div className="max-w-screen-xl mx-auto px-6 flex items-center justify-between h-full gap-8">

          {/* Product mark */}
          <div className="flex flex-col justify-center leading-none shrink-0">
            <span className="font-display font-bold tracking-[0.2em] text-sm uppercase text-foreground">
              SEC·REVIEW
            </span>
            <span className="text-[10px] text-muted-foreground font-mono tracking-wider mt-0.5">
              v·MATRIX
            </span>
          </div>

          {/* Route links */}
          <div className="flex items-center gap-0 flex-1">
            {NAV_LINKS.map(({ to, label, end }, idx) => (
              <div key={to} className="flex items-center">
                {idx > 0 && (
                  <span
                    className="text-muted-foreground/40 font-mono text-xs select-none mx-2"
                    aria-hidden="true"
                  >
                    │
                  </span>
                )}
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    [
                      'font-mono text-xs uppercase tracking-[0.15em] transition-colors',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-sm px-1 py-0.5',
                      isActive
                        ? 'text-primary nav-cursor'
                        : 'text-muted-foreground hover:text-foreground',
                    ].join(' ')
                  }
                >
                  {label}
                </NavLink>
              </div>
            ))}
          </div>

          {/* Right cluster: status lamp + theme toggle */}
          <div className="flex items-center gap-3 shrink-0">
            <div className="flex items-center gap-1.5" aria-label="System status: online">
              <span className="h-1.5 w-1.5 rounded-full bg-signal-success animate-pulse" />
              <span className="text-[10px] text-muted-foreground tracking-wider uppercase font-mono">
                ONLINE
              </span>
            </div>
            <ThemeToggle />
          </div>

        </div>
      </nav>
    </>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
        <NavBar />
        <main className="max-w-screen-xl mx-auto px-4 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/experiments/new" element={<ExperimentNew />} />
            <Route path="/compare" element={<RunCompare />} />
            <Route path="/experiments/:id/compare" element={<RunCompare />} />
            <Route path="/experiments/:experimentId/runs/:runId" element={<RunDetail />} />
            <Route path="/experiments/:id" element={<ExperimentDetail />} />
            <Route path="/datasets/discover" element={<CVEDiscovery />} />
            <Route path="/datasets/:name/source" element={<DatasetSourceView />} />
            <Route path="/datasets/:name" element={<DatasetDetail />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/findings" element={<Findings />} />
            <Route path="/feedback" element={<Feedback />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
