import { PageHeader } from '../components/PageHeader'

export function ProjectArchivePage() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Project Archive" description="What archived project information exists?" />
      <div className="flex-1 overflow-y-auto p-6">
        <p className="text-sm text-slate-400">Archive view — historical engagement records, deliverables, and summaries.</p>
      </div>
    </div>
  )
}
