import { PageHeader } from '../components/PageHeader'
import { TokenBudgetPanel } from '../components/TokenBudgetPanel'
import { EmptyState } from '../components/primitives/EmptyState'
import type { TokenEstimate } from '../api/client'

interface TokenBudgetPageProps {
  estimate: TokenEstimate | null
}

export function TokenBudgetPage({ estimate }: TokenBudgetPageProps) {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Token Budget" description="What external-operation cost estimates exist?" />
      <div className="flex-1 overflow-y-auto p-6">
        {estimate ? (
          <TokenBudgetPanel estimate={estimate} />
        ) : (
          <EmptyState message="No token estimates yet. Select or validate a command in Commands to see estimates." />
        )}
      </div>
    </div>
  )
}
