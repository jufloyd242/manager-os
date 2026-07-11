import { PageHeader } from '../components/PageHeader'
import { DealsView } from '../features/deals/DealsView'

export function DealsPage() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Deals" description="Which deals need attention and why?" />
      <div className="flex-1 overflow-hidden">
        <DealsView />
      </div>
    </div>
  )
}
