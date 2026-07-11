import type { ReactNode } from 'react'

interface LayoutProps {
  children: ReactNode
  currentView?: string
  onViewChange?: (view: string) => void
  badges?: Record<string, number>
}

export function Layout({ children }: LayoutProps) {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-4">
          <h1 className="text-xl font-bold text-slate-900">Command Tower</h1>
          <p className="text-sm text-slate-500">Manager OS</p>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
    </div>
  )
}
