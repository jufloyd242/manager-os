import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import type { ViewId } from './Sidebar'

interface LayoutProps {
  children: ReactNode
  currentView: ViewId
  onViewChange: (view: ViewId) => void
  badges?: {
    daily_loop?: number
    staffing?: number
    archive?: number
  }
}

export function Layout({ children, currentView, onViewChange, badges }: LayoutProps) {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 font-sans">
      {/* Sidebar */}
      <Sidebar currentView={currentView} onViewChange={onViewChange} badges={badges} />

      {/* Main Workspace (Independent Scroll Container) */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Header inside workspace */}
        <header className="border-b border-slate-200 bg-white px-8 py-4 shrink-0 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-extrabold tracking-tight text-slate-900">
              {currentView === 'daily_loop' && 'Command Tower'}
              {currentView === 'staffing' && 'Staffing Center'}
              {currentView === 'meetings' && 'Meetings Calendar'}
              {currentView === 'archive' && 'Project Archive'}
            </h1>
            <p className="text-xs font-medium text-slate-500 mt-0.5">
              {currentView === 'daily_loop' && 'Manager OS — daily operating loop & command console'}
              {currentView === 'staffing' && 'FTE allocation, team health, and capacity forecasting'}
              {currentView === 'meetings' && 'Arbitrary-date calendar, sync, and deterministic preparation'}
              {currentView === 'archive' && 'Historical engagement records, deliverables, and summaries'}
            </p>
          </div>
          
          {/* Top-right subtle accent badge */}
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              Local Synced
            </span>
          </div>
        </header>

        {/* Scrollable content panel */}
        <main className="flex-1 overflow-y-auto px-8 py-6">
          <div className="mx-auto max-w-7xl w-full">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
