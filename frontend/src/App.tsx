import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import BatchNew from './pages/BatchNew'
import BatchDetail from './pages/BatchDetail'
import RunDetail from './pages/RunDetail'
import RunCompare from './pages/RunCompare'
import CVEDiscovery from './pages/CVEDiscovery'
import Datasets from './pages/Datasets'
import DatasetDetail from './pages/DatasetDetail'
import Feedback from './pages/Feedback'
import ThemeToggle from './components/ThemeToggle'

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none ${
    isActive
      ? 'bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-200'
      : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
  }`

function NavBar() {
  return (
    <nav className="bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 sticky top-0 z-50">
      <div className="max-w-screen-xl mx-auto px-4 flex items-center justify-between h-14">
        <div className="flex items-center gap-1">
          <NavLink to="/" end className={navLinkClass} aria-current="page">
            Dashboard
          </NavLink>
          <NavLink to="/batches/new" className={navLinkClass} aria-current="page">
            New Batch
          </NavLink>
          <NavLink to="/datasets" end className={navLinkClass} aria-current="page">
            Datasets
          </NavLink>
          <NavLink to="/datasets/discover" className={navLinkClass} aria-current="page">
            CVE Discovery
          </NavLink>
          <NavLink to="/feedback" className={navLinkClass} aria-current="page">
            Feedback
          </NavLink>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400 dark:text-gray-500 font-mono">sec-review</span>
          <ThemeToggle />
        </div>
      </div>
    </nav>
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
            <Route path="/batches/new" element={<BatchNew />} />
            <Route path="/batches/:id/compare" element={<RunCompare />} />
            <Route path="/batches/:batchId/runs/:runId" element={<RunDetail />} />
            <Route path="/batches/:id" element={<BatchDetail />} />
            <Route path="/datasets/discover" element={<CVEDiscovery />} />
            <Route path="/datasets/:name" element={<DatasetDetail />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/feedback" element={<Feedback />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
