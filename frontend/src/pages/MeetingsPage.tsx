import { PageHeader } from '../components/PageHeader'
import { MeetingsView } from '../components/MeetingsView'

interface MeetingsPageProps {
  initialDate?: string
  initialMeetingId?: string
}

export function MeetingsPage({ initialDate, initialMeetingId }: MeetingsPageProps) {
  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Meetings" description="What meetings do I have and how should I prepare?" />
      <div className="flex-1 overflow-hidden">
        <MeetingsView initialDate={initialDate} initialMeetingId={initialMeetingId} />
      </div>
    </div>
  )
}
