import { useState } from 'react'

export type ViewId = 'daily_loop' | 'staffing' | 'meetings' | 'projects' | 'archive'

interface SidebarProps {
  currentView: ViewId
  onViewChange: (view: ViewId) => void
  badges?: {
    daily_loop?: number
    staffing?: number
    archive?: number
  }
}

export function Sidebar({ currentView, onViewChange, badges }: SidebarProps) {
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const menuItems = [
    {
      id: 'daily_loop' as const,
      label: 'Today',
      badge: badges?.daily_loop,
      icon: (
        <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
      )
    },
    {
      id: 'staffing' as const,
      label: 'People',
      badge: badges?.staffing,
      icon: (
        <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
        </svg>
      )
    },
    {
      id: 'meetings' as const,
      label: 'Meetings',
      icon: (
        <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      )
    },
    {
      id: 'projects' as const,
      label: 'Projects',
      icon: (
        <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 002-2h2a2 2 0 002 2" />
        </svg>
      )
    }
  ]

  const advancedItems = [
    {
      id: 'archive' as const,
      label: 'Project Archive',
      badge: badges?.archive,
      icon: (
        <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 4H6a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-2m-4-1v8m0 0l3-3m-3 3L9 8m-5 5h2.586a1 1 0 01.707.293l2.414 2.414a1 1 0 00.707.293h3.172a1 1 0 00.707-.293l2.414-2.414a1 1 0 01.707-.293H20" />
        </svg>
      )
    }
  ]

  return (
    <aside
      className={`relative flex flex-col bg-slate-900 text-slate-300 border-r border-slate-800 transition-all duration-300 ease-in-out select-none shrink-0 ${
        isCollapsed ? 'w-16' : 'w-64'
      }`}
    >
      {/* Brand Header */}
      <div className="flex h-16 items-center justify-between px-4 border-b border-slate-800">
        <div className="flex items-center gap-3 overflow-hidden">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white shrink-0 shadow-md shadow-indigo-500/20">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
          </div>
          {!isCollapsed && (
            <span className="font-bold text-sm tracking-wider uppercase text-white animate-fade-in whitespace-nowrap">
              Manager OS
            </span>
          )}
        </div>
      </div>

      {/* Navigation Items */}
      <nav className="flex-1 space-y-1.5 px-3 py-4 overflow-y-auto">
        {menuItems.map((item) => {
          const isActive = currentView === item.id
          return (
            <button
              key={item.id}
              onClick={() => onViewChange(item.id)}
              className={`w-full relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 cursor-pointer ${
                isActive
                  ? 'bg-indigo-600/90 text-white shadow-md shadow-indigo-500/10'
                  : 'hover:bg-slate-800 hover:text-slate-100 text-slate-400'
              }`}
            >
              {item.icon}
              {!isCollapsed && (
                <span className="truncate whitespace-nowrap">{item.label}</span>
              )}
              {item.badge !== undefined && (
                <span
                  data-testid={`nav-badge-${item.id}`}
                  className={`px-2 py-0.5 rounded-full text-xs font-bold transition-all duration-200 shrink-0 ${
                    isCollapsed
                      ? 'absolute -top-1 -right-1 scale-75 bg-indigo-500 text-white shadow-sm'
                      : isActive
                      ? 'ml-auto bg-indigo-500 text-white'
                      : 'ml-auto bg-slate-800 text-slate-400'
                  }`}
                >
                  {item.badge}
                </span>
              )}
            </button>
          )
        })}

        {/* Advanced Collapsible Section */}
        <div className="pt-4 border-t border-slate-800 mt-4">
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="w-full flex items-center justify-between px-3 py-2 text-xs font-bold uppercase tracking-wider text-slate-500 hover:text-slate-300 transition-colors"
          >
            {!isCollapsed && <span>Advanced</span>}
            <svg
              className={`w-4 h-4 transition-transform duration-200 ${showAdvanced ? 'rotate-90' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7" />
            </svg>
          </button>
          {showAdvanced && advancedItems.map((item) => {
            const isActive = currentView === item.id
            return (
              <button
                key={item.id}
                onClick={() => onViewChange(item.id)}
                className={`w-full relative flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium transition-all duration-200 cursor-pointer mt-1.5 ${
                  isActive
                    ? 'bg-indigo-600/90 text-white shadow-md shadow-indigo-500/10'
                    : 'hover:bg-slate-800 hover:text-slate-100 text-slate-400'
                }`}
              >
                {item.icon}
                {!isCollapsed && (
                  <span className="truncate whitespace-nowrap">{item.label}</span>
                )}
                {item.badge !== undefined && (
                  <span
                    className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold shrink-0 ml-auto ${
                      isActive ? 'bg-indigo-500 text-white' : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </nav>

      {/* Collapsible toggle action button at the bottom */}
      <div className="p-3 border-t border-slate-800">
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          aria-label="Collapse sidebar"
          className="w-full flex items-center justify-center p-2 rounded-lg text-slate-500 hover:bg-slate-800 hover:text-slate-200 transition-colors cursor-pointer"
        >
          <svg
            className={`w-5 h-5 transition-transform duration-300 ${isCollapsed ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
      </div>
    </aside>
  )
}
