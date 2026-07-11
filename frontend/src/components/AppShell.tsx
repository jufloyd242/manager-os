import type { ReactNode } from 'react'
import { useState, useEffect } from 'react'
import { Sidebar } from './Sidebar'
import type { Route } from '../hooks/useHashRoute'

interface AppShellProps {
  currentRoute: Route
  onNavigate: (route: Route) => void
  children: ReactNode
  badges?: Record<string, number>
}

export function AppShell({ currentRoute, onNavigate, children, badges }: AppShellProps) {
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false)

  useEffect(() => {
    setMobileDrawerOpen(false)
  }, [currentRoute])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && mobileDrawerOpen) {
        setMobileDrawerOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [mobileDrawerOpen])

  return (
    <div className="flex h-screen overflow-hidden bg-slate-100">
      {/* Desktop sidebar */}
      <div className="hidden md:block shrink-0">
        <Sidebar currentRoute={currentRoute} onNavigate={onNavigate} badges={badges} />
      </div>

      {/* Mobile drawer */}
      {mobileDrawerOpen && (
        <>
          <div
            className="fixed inset-0 bg-black/40 z-40 md:hidden"
            onClick={() => setMobileDrawerOpen(false)}
            aria-hidden="true"
          />
          <div className="fixed left-0 top-0 bottom-0 z-50 md:hidden">
            <Sidebar currentRoute={currentRoute} onNavigate={onNavigate} badges={badges} />
          </div>
        </>
      )}

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile top bar */}
        <div className="md:hidden flex items-center gap-3 px-4 h-14 border-b border-slate-200 bg-white shrink-0">
          <button
            onClick={() => setMobileDrawerOpen(true)}
            className="p-2 rounded-lg hover:bg-slate-100 cursor-pointer"
            aria-label="Open navigation menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="font-bold text-sm text-slate-900">Manager OS</span>
        </div>

        {/* Page workspace */}
        <div className="flex-1 overflow-hidden">{children}</div>
      </div>
    </div>
  )
}
