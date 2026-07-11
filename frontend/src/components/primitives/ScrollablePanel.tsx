import type { ReactNode } from 'react'

interface ScrollablePanelProps {
  children: ReactNode
  className?: string
}

export function ScrollablePanel({ children, className = '' }: ScrollablePanelProps) {
  return (
    <div className={`overflow-y-auto ${className}`}>
      {children}
    </div>
  )
}
