import { useState, useEffect, useCallback } from 'react'

export type Route =
  | 'today'
  | 'actions'
  | 'meetings'
  | 'deals'
  | 'forecast'
  | 'workspace'
  | 'people'
  | 'projects'
  | 'data-health'
  | 'refresh-history'
  | 'commands'
  | 'run-history'
  | 'token-budget'
  | 'project-archive'

export const ALL_ROUTES: Route[] = [
  'today', 'actions', 'meetings', 'deals', 'forecast',
  'workspace', 'people', 'projects', 'data-health', 'refresh-history',
  'commands', 'run-history', 'token-budget', 'project-archive',
]

function parseHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, '').trim()
  if (!hash) return 'today'
  if (ALL_ROUTES.includes(hash as Route)) return hash as Route
  return 'today'
}

export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(parseHash)

  useEffect(() => {
    const handler = () => setRoute(parseHash())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  const navigate = useCallback((newRoute: Route) => {
    window.location.hash = `/${newRoute}`
  }, [])

  return [route, navigate]
}
