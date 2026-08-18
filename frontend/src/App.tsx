import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import { ApplicationCreatePage } from './pages/ApplicationCreatePage'
import { ApplicationDetailPage } from './pages/ApplicationDetailPage'
import { ApplicationsListPage } from './pages/ApplicationsListPage'
import { DocumentsPage } from './pages/DocumentsPage'

/**
 * Routing.
 *
 * `BrowserRouter` maps the browser's URL to a component. Real URLs (rather than
 * a screen kept in state) mean the back button, refresh, and bookmarking all
 * work the way a user expects — `/applications/7` always opens application 7.
 */
export function App() {
  return (
    <BrowserRouter>
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
        </nav>
      </header>

      <main className="container">
        <Routes>
          <Route path="/" element={<ApplicationsListPage />} />
          <Route path="/applications/new" element={<ApplicationCreatePage />} />
          <Route path="/applications/:id" element={<ApplicationDetailPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
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
