import { PageHeader } from '../components/PageHeader'
import { ForecastView } from '../features/forecast/ForecastView'

export function ForecastPage() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Forecast" description="Who has a staffing exception and why?" />
      <div className="flex-1 overflow-hidden">
        <ForecastView />
      </div>
    </div>
  )
}
