import { useState, useEffect } from 'react'
import type { Route } from '../hooks/useHashRoute'

interface SidebarProps {
  currentRoute: Route
  onNavigate: (route: Route) => void
  badges?: Record<string, number>
}

interface NavItem {
  id: Route
  label: string
  badge?: string
  icon: React.ReactNode
}

interface NavGroup {
  label: string
  items: NavItem[]
}

function formatBadge(n: number | undefined): string | undefined {
  if (n === undefined) return undefined
  if (n > 99) return '99+'
  return String(n)
}

export function Sidebar({ currentRoute, onNavigate, badges = {} }: SidebarProps) {
  const [isCollapsed, setIsCollapsed] = useState(() => {
    return localStorage.getItem('manager-os-sidebar-collapsed') === 'true'
  })
  const [showAdvanced, setShowAdvanced] = useState(() => {
    return localStorage.getItem('manager-os-advanced-expanded') === 'true'
  })

  useEffect(() => {
    localStorage.setItem('manager-os-sidebar-collapsed', String(isCollapsed))
  }, [isCollapsed])

  useEffect(() => {
    localStorage.setItem('manager-os-advanced-expanded', String(showAdvanced))
  }, [showAdvanced])

  const icon = (path: string) => (
    <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={path} />
    </svg>
  )

  const groups: NavGroup[] = [
    {
      label: 'Today',
      items: [
        { id: 'today', label: 'Today', badge: formatBadge(badges.today), icon: icon('M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z') },
        { id: 'actions', label: 'Actions', badge: formatBadge(badges.actions), icon: icon('M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4') },
      ],
    },
    {
      label: 'Work',
      items: [
        { id: 'meetings', label: 'Meetings', badge: formatBadge(badges.meetings), icon: icon('M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z') },
        { id: 'deals', label: 'Deals', badge: formatBadge(badges.deals), icon: icon('M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z') },
        { id: 'forecast', label: 'Forecast', badge: formatBadge(badges.forecast), icon: icon('M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z') },
      ],
    },
    {
      label: 'Context',
      items: [
        { id: 'workspace', label: 'Workspace', icon: icon('M3 7v10a2 2 0 002 2h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2zM3 7l9 6 9-6') },
        { id: 'people', label: 'People', icon: icon('M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-1.13a4 4 0 10-4-4 4 4 0 004 4zm6 0a4 4 0 10-4-4 4 4 0 004 4z') },
        { id: 'projects', label: 'Projects', icon: icon('M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10') },
      ],
    },
    {
      label: 'Operations',
      items: [
        { id: 'data-health', label: 'Data Health', badge: formatBadge(badges['data-health']), icon: icon('M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z') },
        { id: 'refresh-history', label: 'Operation History', icon: icon('M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15') },
      ],
    },
  ]

  const advancedItems: NavItem[] = [
    { id: 'commands', label: 'Commands', icon: icon('M8 9l3 3-3 3m5 0h3M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z') },
    { id: 'run-history', label: 'Run History', icon: icon('M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z M5 7h14M5 12h14M5 17h14') },
    { id: 'token-budget', label: 'Token Budget', icon: icon('M7 7h.01M7 3h5a1.99 1.99 0 011.4.6l3.8 3.8a2 2 0 01.6 1.4V19a2 2 0 01-2 2H7a2 2 0 01-2-2V5a2 2 0 012-2z') },
    { id: 'project-archive', label: 'Project Archive', icon: icon('M8 4H6a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-2m-4-1v8m0 0l3-3m-3 3L9 8m-5 5h2.586a1 1 0 01.707.293l2.414 2.414a1 1 0 00.707.293h3.172a1 1 0 00.707-.293l2.414-2.414a1 1 0 01.707-.293H20') },
  ]

  const renderItem = (item: NavItem) => {
    const isActive = currentRoute === item.id
    return (
      <button
        key={item.id}
        onClick={() => onNavigate(item.id)}
        title={isCollapsed ? item.label : undefined}
        className={`w-full relative flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium transition-all duration-200 cursor-pointer ${
          isActive
            ? 'bg-indigo-600/90 text-white shadow-md shadow-indigo-500/10'
            : 'hover:bg-slate-800 hover:text-slate-100 text-slate-400'
        }`}
      >
        {item.icon}
        {!isCollapsed && <span className="truncate whitespace-nowrap">{item.label}</span>}
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
  }

  return (
    <aside
      className={`relative flex flex-col bg-slate-900 text-slate-300 border-r border-slate-800 transition-all duration-300 ease-in-out select-none shrink-0 h-full ${
        isCollapsed ? 'w-16' : 'w-56'
      }`}
    >
      {/* Brand Header */}
      <div className="flex h-14 items-center px-4 border-b border-slate-800 shrink-0">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white shrink-0">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
        </div>
        {!isCollapsed && (
          <span className="ml-3 font-bold text-sm tracking-wider uppercase text-white whitespace-nowrap">
            Manager OS
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-3">
        {groups.map((group) => (
          <div key={group.label}>
            {!isCollapsed && (
              <p className="px-3 mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-600">{group.label}</p>
            )}
            <div className="space-y-0.5">
              {group.items.map(renderItem)}
            </div>
          </div>
        ))}

        {/* Advanced */}
        <div>
          {!isCollapsed && (
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="w-full px-3 mb-1 text-[10px] font-bold uppercase tracking-wider text-slate-600 hover:text-slate-400 flex items-center justify-between cursor-pointer"
            >
              <span>Advanced</span>
              <span className={`transition-transform ${showAdvanced ? 'rotate-90' : ''}`}>▶</span>
            </button>
          )}
          {(showAdvanced || isCollapsed) && (
            <div className="space-y-0.5">
              {advancedItems.map(renderItem)}
            </div>
          )}
        </div>
      </nav>

      {/* Collapse toggle */}
      <div className="p-2 border-t border-slate-800 shrink-0">
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          aria-label="Collapse sidebar"
          className="w-full flex items-center justify-center p-2 rounded-lg text-slate-500 hover:bg-slate-800 hover:text-slate-200 transition-colors cursor-pointer"
        >
          <svg
            className={`w-5 h-5 transition-transform duration-300 ${isCollapsed ? 'rotate-180' : ''}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
      </div>
    </aside>
  )
}
