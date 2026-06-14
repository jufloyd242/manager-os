# Daily Brief — {{ brief_date }}

**{{ total_signals }} signal(s) requiring attention** | {{ open_action_items }} open action item(s) | {{ meeting_count }} meeting(s) today

---
{% if critical_signals %}
## 🔴 Critical — Immediate Action Required
{% for s in critical_signals %}
- **[{{ s.entity_type | upper }}] {{ s.entity_name }}** — {{ s.summary }}{% if s.due_date %} *(due {{ s.due_date }})* {% endif %}
  > {{ s.why_it_matters }}
{% endfor %}
{% endif %}
{% if risk_signals %}
## 🚨 Delivery Risks
{% for s in risk_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}{% if s.due_date %} *(due {{ s.due_date }})* {% endif %}
  > *Source: {{ s.source }}*
{% endfor %}
{% endif %}
{% if people_signals %}
## 👥 People Needing Attention
{% for s in people_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
{% endfor %}
{% endif %}
{% if deal_signals %}
## 📋 Deal / SOW / LOE Actions
{% for s in deal_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}{% if s.due_date %} *(due {{ s.due_date }})* {% endif %}
  > {{ s.why_it_matters }}
{% endfor %}
{% endif %}
{% if utilization_signals %}
## ⚠️ Utilization / Staffing Risks
{% for s in utilization_signals %}
- **{{ s.entity_name }}** — {{ s.summary }}
{% endfor %}
{% endif %}
{% if other_signals %}
## 📌 Other Signals
{% for s in other_signals %}
- **[{{ s.signal_type }}] {{ s.entity_name }}** — {{ s.summary }}
{% endfor %}
{% endif %}
{% if action_items %}
## ✅ Open Action Items
{% for ai in action_items %}
- [ ] **{{ ai.assigned_to }}**: {{ ai.description }}{% if ai.due_date %} *(by {{ ai.due_date }})* {% endif %}
{% endfor %}
{% endif %}
{% if meetings %}
## 📅 Today's Meetings
{% for m in meetings %}
- **{{ m.start_time or 'TBD' }}** — {{ m.title }}{% if m.attendees %} ({{ m.attendees | join(', ') }}){% endif %}
{% endfor %}
{% endif %}

---
*Generated {{ generated_at }} | {{ total_signals }} signals, {{ open_action_items }} action items*
