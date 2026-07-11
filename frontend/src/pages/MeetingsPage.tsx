import { PageHeader } from '../components/PageHeader'
import { MeetingsView } from '../components/MeetingsView'

export function MeetingsPage() {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Meetings" description="What meetings do I have and how should I prepare?" />
      <div className="flex-1 overflow-hidden">
        <MeetingsView />
      </div>
    </div>
  )
}
