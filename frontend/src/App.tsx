import { BrowserRouter, Routes, Route, NavLink, Link, useLocation } from 'react-router-dom'
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
import StrategiesList from './pages/StrategiesList'
import StrategyViewer from './pages/StrategyViewer'
import StrategyEditor from './pages/StrategyEditor'
import ExperimentImport from './pages/ExperimentImport'
import Settings from './pages/Settings'
import NotFound from './pages/NotFound'
import ThemeToggle from './components/ThemeToggle'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from './components/ui/dropdown-menu'
import { ChevronDown } from 'lucide-react'

const NAV_GROUPS = [
  {
    label: 'Inputs',
    paths: ['/strategies', '/datasets', '/datasets/discover'],
    items: [
      { to: '/strategies', label: 'Strategies' },
      { to: '/datasets', label: 'Datasets' },
      { to: '/datasets/discover', label: 'CVE Discovery' },
    ],
  },
  {
    label: 'Experiments',
    paths: ['/experiments/new', '/experiments/import', '/compare'],
    items: [
      { to: '/experiments/new', label: 'New Experiment' },
      { to: '/experiments/import', label: 'Import' },
      { to: '/compare', label: 'Compare' },
    ],
  },
  {
    label: 'Results',
    paths: ['/findings', '/feedback'],
    items: [
      { to: '/findings', label: 'Findings' },
      { to: '/feedback', label: 'Feedback' },
    ],
  },
]

const isInGroup = (paths: string[], pathname: string) =>
  paths.some(p => p === '/' ? pathname === '/' : pathname === p || pathname.startsWith(p + '/'))

const navBaseClass =
  'font-mono text-xs uppercase tracking-[0.15em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-sm px-1 py-0.5'
const navActiveClass = 'text-primary nav-cursor'
const navInactiveClass = 'text-muted-foreground hover:text-foreground'

function NavDivider() {
  return (
    <span className="text-muted-foreground/40 font-mono text-xs select-none mx-2" aria-hidden="true">
      │
    </span>
  )
}

export function NavBar() {
  const { pathname } = useLocation()

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

            {/* Dashboard */}
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                [navBaseClass, isActive ? navActiveClass : navInactiveClass].join(' ')
              }
            >
              Dashboard
            </NavLink>

            {/* Dropdown groups */}
            {NAV_GROUPS.map((group) => {
              const active = isInGroup(group.paths, pathname)
              return (
                <div key={group.label} className="flex items-center">
                  <NavDivider />
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      className={[
                        navBaseClass,
                        active ? navActiveClass : navInactiveClass,
                        'flex items-center gap-0.5',
                      ].join(' ')}
                    >
                      {group.label}
                      <ChevronDown className="size-3 text-muted-foreground ml-0.5" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {group.items.map((item) => (
                        <DropdownMenuItem key={item.to} asChild>
                          <Link
                            to={item.to}
                            className="font-mono text-xs uppercase tracking-[0.15em] w-full"
                          >
                            {item.label}
                          </Link>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )
            })}

            {/* Settings */}
            <div className="flex items-center">
              <NavDivider />
              <NavLink
                to="/settings"
                className={({ isActive }) =>
                  [navBaseClass, isActive ? navActiveClass : navInactiveClass].join(' ')
                }
              >
                Settings
              </NavLink>
            </div>

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
            <Route path="/experiments/import" element={<ExperimentImport />} />
            <Route path="/experiments/:id" element={<ExperimentDetail />} />
            <Route path="/strategies/new" element={<StrategyEditor />} />
            <Route path="/strategies/:id/fork" element={<StrategyEditor />} />
            <Route path="/strategies/:id" element={<StrategyViewer />} />
            <Route path="/strategies" element={<StrategiesList />} />
            <Route path="/datasets/discover" element={<CVEDiscovery />} />
            <Route path="/datasets/:name/source" element={<DatasetSourceView />} />
            <Route path="/datasets/:name" element={<DatasetDetail />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/findings" element={<Findings />} />
            <Route path="/feedback" element={<Feedback />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
