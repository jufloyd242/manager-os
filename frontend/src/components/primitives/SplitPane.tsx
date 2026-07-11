import type { ReactNode } from 'react'

interface SplitPaneProps {
  left: ReactNode
  right: ReactNode
  leftClassName?: string
  rightClassName?: string
}

export function SplitPane({ left, right, leftClassName = '', rightClassName = '' }: SplitPaneProps) {
  return (
    <div className="flex-1 flex overflow-hidden">
      <div className={`w-1/2 overflow-y-auto border-r border-slate-200 ${leftClassName}`}>
        {left}
      </div>
      <div className={`w-1/2 overflow-y-auto ${rightClassName}`}>
        {right}
      </div>
    </div>
  )
}
