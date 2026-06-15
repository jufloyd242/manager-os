# Daily Brief -- {{ brief_date }}

{% if quality_filtered %}
Showing {{ shown_total }} of {{ total_candidates }} candidate item(s) after quality filters.
{% else %}
Showing {{ shown_total }} of {{ total_candidates }} candidate item(s).
{% endif %}
Signals: {{ total_signals }} | Follow-ups: {{ total_follow_ups }} | Waiting on: {{ total_waiting_on }} | Decisions: {{ total_decisions }} | Meetings today: {{ meeting_count }}
{% if total_hidden > 0 %}
*{{ total_hidden }} item(s) not shown -- see dashboard*
{% endif %}
{% if suppressed_count > 0 %}
*{{ suppressed_count }} duplicate signal(s) suppressed*
{% endif %}

---
{% if critical_signals %}
## Critical -- Act Now
{% for s in critical_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
  - Why: {{ s.why_it_matters }}
{% if s.due_date %}  - Due: {{ s.due_date }}
{% endif %}  - Source: {{ s.source_path | readable_path }}
{% endfor %}

{% endif %}
{% if deal_signals %}
## Deals / Pipeline
{% for s in deal_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
  - Why: {{ s.why_it_matters }}
{% if s.due_date %}  - Due: {{ s.due_date }}
{% endif %}  - Source: {{ s.source_path | readable_path }}
{% endfor %}
{% if overflow.deals > 0 %}
*{{ overflow.deals }} additional deal signal(s) not shown.*
{% endif %}

{% endif %}
{% if utilization_signals %}
## Capacity / Staffing
{% for s in utilization_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
  - Why: {{ s.why_it_matters }}
{% if s.source_path %}  - Source: {{ s.source_path | readable_path }}
{% endif %}{% endfor %}
{% if overflow.utilization > 0 %}
*{{ overflow.utilization }} additional staffing signal(s) not shown.*
{% endif %}

{% endif %}
{% if decisions %}
## Decisions Owed
{% for d in decisions %}
- **{{ d.entity_name or 'General' }}**: {{ d.description }}
{% if d.decision_date %}  - By: {{ d.decision_date }}
{% endif %}{% endfor %}
{% if overflow.decisions > 0 %}
*{{ overflow.decisions }} additional decision(s) not shown.*
{% endif %}

{% endif %}
{% if risk_signals %}
## Top Risks
{% for s in risk_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
  - Why: {{ s.why_it_matters }}
  - Source: {{ s.source_path | readable_path }}
{% endfor %}
{% if overflow.risks > 0 %}
*{{ overflow.risks }} additional risk signal(s) not shown.*
{% endif %}

{% endif %}
{% if follow_ups %}
## Follow-ups You Owe
{% for ai in follow_ups %}
- [ ] {{ ai.description }}
{% if ai.due_date %}  - By: {{ ai.due_date }}
{% endif %}{% endfor %}
{% if overflow.follow_ups > 0 %}
*{{ overflow.follow_ups }} additional follow-up(s) not shown.*
{% endif %}

{% endif %}
{% if people_signals %}
## People
{% for s in people_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
{% if s.source_path %}  - Source: {{ s.source_path | readable_path }}
{% endif %}{% endfor %}
{% if overflow.people > 0 %}
*{{ overflow.people }} additional people signal(s) not shown.*
{% endif %}

{% endif %}
{% if meetings %}
## Meetings Today
{% for m in meetings %}
- **{{ m.start_time or 'TBD' }}** — {{ m.title }}
{% if m.attendees %}  - Attendees: {{ m.attendees | length }}
{% endif %}{% endfor %}
{% if overflow.meetings > 0 %}
*{{ overflow.meetings }} additional meeting(s) not shown.*
{% endif %}

{% endif %}
{% if other_action_items %}
## Waiting On / Others
{% for ai in other_action_items %}
- **{{ ai.assigned_to }}**: {{ ai.description }}
{% if ai.due_date %}  - By: {{ ai.due_date }}
{% endif %}{% endfor %}
{% if overflow.waiting_on > 0 %}
*{{ overflow.waiting_on }} additional waiting-on item(s) not shown.*
{% endif %}

{% endif %}
{% if other_signals %}
## Other Signals
{% for s in other_signals %}
- **[{{ s.signal_type }}] {{ s.entity_name }}** — {{ s.summary }}
  - Source: {{ s.source_path | readable_path }}
{% endfor %}
{% if overflow.other > 0 %}
*{{ overflow.other }} additional signal(s) not shown.*
{% endif %}

{% endif %}

---
*Generated {{ generated_at }} | {{ shown_total }} of {{ total_candidates }} items shown ({{ total_signals }} signals · {{ total_follow_ups }} follow-ups · {{ total_waiting_on }} waiting-on)*
