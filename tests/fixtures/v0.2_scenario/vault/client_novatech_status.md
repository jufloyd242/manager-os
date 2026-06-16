---
type: client
entity: NovaTech Inc
date: 2026-06-10
tags: [client, novatech, status, risk]
manager_os: active
---

# NovaTech Inc — Client Status Update — 2026-06-10

## Overall health
Red. Delivery is behind and there is a credible escalation risk.

## Current team
- Jordan Lee (lead ML engineer, 100% allocated)
- Sam Rivera (data engineer, 80% allocated)

## Risks
1. **Model drift alert** — The production model is degrading. Client flagged a 12% accuracy drop on Monday. This is **at risk** of turning into a formal escalation if not addressed by end of this week.
2. **Data pipeline delay** — Client data team has been slow to provide updated training data. Now 10 days delayed. This is **blocked** by client approval process.
3. **Contractual milestone** — NovaTech Phase 1 delivery is due June 21. The team is **concerned** about hitting this milestone without the updated training data.

## Client sentiment
The VP of Data is frustrated. The weekly status call last Friday was tense. We need to proactively address the pipeline delay and the model drift before the next touchpoint.

## Next milestone
Phase 1 model delivery: June 21, 2026.

## Unresolved decisions
- Should we deploy a fallback rule-based model while the ML model is retrained?
- Who owns the data quality remediation — NovaTech or our team?

## Action items
- I need to follow up with NovaTech VP of Data by EOW to discuss escalation path
- Need to loop in AE on the contractual milestone risk
- Waiting on NovaTech data team to deliver updated training set
