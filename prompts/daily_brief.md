# Daily Brief -- {{ brief_date }}

**Showing {{ shown_signals }} of {{ total_signals }} signal(s)** | **{{ open_action_items }} follow-up(s)** | **{{ meeting_count }} meeting(s) today**
{%- if total_hidden > 0 %}
*{{ total_hidden }} lower-priority item(s) hidden -- see dashboard*
{%- endif %}
{%- if suppressed_count > 0 %}
*{{ suppressed_count }} duplicate signal(s) suppressed*
{%- endif %}

---
{% if critical_signals %}
## Critical -- Act Now
{% for s in critical_signals %}
- **[{{ s.entity_name }}]** {{ s.summary }}
{% if s.due_date %}  *(due {{ s.due_date }})*
{% endif %}{% if s.why_it_matters %}  > {{ s.why_it_matters }}
{% endif %}{% if s.source_path %}  *Source: {{ s.source_path | basename }}*
{% endif %}{% endfor %}
{% endif %}
{% if decisions %}
## Decisions Needed
{% for d in decisions %}
- **{{ d.entity_name or 'General' }}**: {{ d.description }}
{% if d.decision_date %}  *(by {{ d.decision_date }})*
{% endif %}{% endfor %}
{% if overflow.decisions > 0 %}*{{ overflow.decisions }} additional decision(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if risk_signals %}
## Top Risks
{% for s in risk_signals %}
- **{{ s.entity_name }}** -- {{ s.summary }}
{% if s.due_date %}  *(due {{ s.due_date }})*
{% endif %}{% if s.source_path %}  *Source: {{ s.source_path | basename }}*
{% endif %}{% if s.why_it_matters %}  > {{ s.why_it_matters }}
{% endif %}{% endfor %}
{% if overflow.risks > 0 %}*{{ overflow.risks }} additional risk signal(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if follow_ups %}
## Follow-ups You Owe
{% for ai in follow_ups %}
- [ ] {{ ai.description }}
{% if ai.due_date %}  *(by {{ ai.due_date }})*
{% endif %}{% endfor %}
{% if overflow.follow_ups > 0 %}*{{ overflow.follow_ups }} additional follow-up(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if people_signals %}
## People Needing Attention
{% for s in people_signals %}
- **{{ s.entity_name }}** -- {{ s.summary }}
{% if s.source_path %}  *Source: {{ s.source_path | basename }}*
{% endif %}{% endfor %}
{% if overflow.people > 0 %}*{{ overflow.people }} additional people signal(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if deal_signals %}
## Deal / SOW / LOE Asks
{% for s in deal_signals %}
- **{{ s.entity_name }}** -- {{ s.summary }}
{% if s.due_date %}  *(due {{ s.due_date }})*
{% endif %}{% if s.source_path %}  *Source: {{ s.source_path | basename }}*
{% endif %}{% if s.why_it_matters %}  > {{ s.why_it_matters }}
{% endif %}{% endfor %}
{% if overflow.deals > 0 %}*{{ overflow.deals }} additional deal signal(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if utilization_signals %}
## Staffing / Utilization
{% for s in utilization_signals %}
- **{{ s.entity_name }}** -- {{ s.summary }}
{% if s.source_path %}  *Source: {{ s.source_path | basename }}*
{% endif %}{% endfor %}
{% if overflow.utilization > 0 %}*{{ overflow.utilization }} additional utilization signal(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if meetings %}
## Meetings Needing Prep
{% for m in meetings %}
- **{{ m.start_time or 'TBD' }}** -- {{ m.title }}
{% if m.attendees %}  *({{ m.attendees | length }} attendee(s))*
{% endif %}{% endfor %}
{% if overflow.meetings > 0 %}*{{ overflow.meetings }} additional meeting(s) hidden -- see dashboard.*{% endif %}
{% endif %}
{% if other_action_items %}
## Waiting On / Others
{% for ai in other_action_items %}
- **{{ ai.assigned_to }}**: {{ ai.description }}
{% if ai.due_date %}  *(by {{ ai.due_date }})*
{% endif %}{% endfor %}
{% endif %}
{% if other_signals %}
## Other Signals
{% for s in other_signals %}
- **[{{ s.signal_type }}] {{ s.entity_name }}** -- {{ s.summary }}
{% endfor %}
{% if overflow.other > 0 %}*{{ overflow.other }} additional signal(s) hidden -- see dashboard.*{% endif %}
{% endif %}

---
*Generated {{ generated_at }} | showing {{ shown_signals }} of {{ total_signals }} total signals, {{ open_action_items }} open follow-ups*
