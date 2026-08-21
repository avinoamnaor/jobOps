import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { listSuggestions } from './api/suggestions'
import { useAsync } from './hooks/useAsync'
import { ApplicationCreatePage } from './pages/ApplicationCreatePage'
import { ApplicationDetailPage } from './pages/ApplicationDetailPage'
import { ApplicationsListPage } from './pages/ApplicationsListPage'
import { DocumentsPage } from './pages/DocumentsPage'
import { SuggestionsPage } from './pages/SuggestionsPage'

/**
 * The "Needs attention" nav badge: how many suggestions are pending review.
 *
 * Refetches whenever the route changes (`location.key` is unique per
 * navigation), which is enough to stay accurate as you move between pages —
 * without polling or a global store, which this app deliberately has neither of.
 */
function SuggestionsNavLink() {
  const location = useLocation()
  const pending = useAsync(() => listSuggestions('pending'), [location.key])
  const count = pending.data?.length ?? 0

  return (
    <NavLink to="/suggestions" className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
      Needs attention
      {count > 0 && <span className="nav-badge">{count}</span>}
    </NavLink>
  )
}

/**
 * Routing.
 *
 * `BrowserRouter` maps the browser's URL to a component. Real URLs (rather than
 * a screen kept in state) mean the back button, refresh, and bookmarking all
 * work the way a user expects — `/applications/7` always opens application 7.
 *
 * The `future` flags opt in early to two React Router v7 behaviours, which also
 * silences the dev-console deprecation warnings. `v7_startTransition` wraps
 * router state updates in React.startTransition (safe here — no Suspense);
 * `v7_relativeSplatPath` has no effect for us since every link uses an absolute
 * path.
 */
export function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <header className="topbar">
        <Link to="/" className="brand">
          JobOps
        </Link>
        <nav className="nav">
          {/* NavLink knows whether its route is the active one. */}
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
            Applications
          </NavLink>
          <NavLink
            to="/documents"
            className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
          >
            Documents
          </NavLink>
          <SuggestionsNavLink />
        </nav>
      </header>

      <main className="container">
        <Routes>
          <Route path="/" element={<ApplicationsListPage />} />
          <Route path="/applications/new" element={<ApplicationCreatePage />} />
          <Route path="/applications/:id" element={<ApplicationDetailPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/suggestions" element={<SuggestionsPage />} />
          <Route
            path="*"
            element={
              <div className="empty">
                <p className="empty-title">That page does not exist.</p>
                <Link className="btn btn-primary" to="/">
                  Back to applications
                </Link>
              </div>
            }
          />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
