import { PageHeader } from '../components/PageHeader'

export function NotFoundPage() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Not Found" description="The page you're looking for doesn't exist." />
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <p className="text-4xl font-bold text-slate-300 mb-2">404</p>
          <p className="text-sm text-slate-500">Use the sidebar to navigate to a valid page.</p>
        </div>
      </div>
    </div>
  )
}
