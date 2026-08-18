import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './index.css'

/**
 * Entry point: attach the React app to the <div id="root"> in index.html.
 *
 * StrictMode is development-only. It deliberately runs effects twice to surface
 * missing cleanup functions — which is exactly why `useAsync` and `useDebounced`
 * both return one.
 */
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
