"""Grouping/summary helpers for `recommended_actions` in the Daily Operating
Loop payload — powers the frontend "command tower" UI (grouped, collapsible
action lists) without changing or truncating `recommended_actions` itself.

Pure functions, no DB access — operate only on the list of action dicts
already produced by `daily_operating_loop._recommended_actions`.
"""

from __future__ import annotations

# Known sources, in the exact deterministic group order required by the
# command tower UI. Any source not in this list (or missing entirely) is
# merged into a single trailing "other" group.
_GROUP_ORDER = [
    "people_staffing",
    "meetings",
    "projects_deals",
    "document_gaps",
    "feedback_learning",
]

_GROUP_TITLES = {
    "people_staffing": "People / Staffing",
    "meetings": "Meetings",
    "projects_deals": "Projects / Deals",
    "document_gaps": "Project Document Gaps",
    "feedback_learning": "Feedback Learning",
    "other": "Other",
}

_GROUP_SUMMARY_TEMPLATES = {
    "people_staffing": "{count} staffing concern(s) need review.",
    "meetings": "{count} meeting(s) need prep.",
    "projects_deals": "{count} project/deal risk signal(s).",
    "feedback_learning": "{count} feedback pattern(s) detected.",
    "other": "{count} other item(s).",
}

_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}

_DEFAULT_VISIBLE_LIMIT = 5


def _is_executable(action: dict) -> bool:
    return bool(action.get("primary_command")) or bool(action.get("secondary_commands"))


def build_action_summary(actions: list[dict]) -> dict:
    """Summarize `recommended_actions` into totals by source/priority plus an
    executable vs. informational split.

    - by_source: keyed by each action's "source" field; actions with no
      "source" are bucketed under "other".
    - by_priority: fixed high/medium/low keys, always present (0 if unused).
    - executable: actions with a truthy primary_command OR a non-empty
      secondary_commands list.
    - informational: everything else.
    """
    by_source: dict[str, int] = {}
    by_priority = {"high": 0, "medium": 0, "low": 0}
    executable = 0
    informational = 0

    for action in actions:
        source = action.get("source") or "other"
        by_source[source] = by_source.get(source, 0) + 1

        priority = action.get("priority")
        if priority in by_priority:
            by_priority[priority] += 1

        if _is_executable(action):
            executable += 1
        else:
            informational += 1

    return {
        "total": len(actions),
        "by_source": by_source,
        "by_priority": by_priority,
        "executable": executable,
        "informational": informational,
    }


def _group_priority(actions: list[dict]) -> str:
    """Dominant priority for a group.

    Convention (documented here since the spec left this as an
    implementation choice): use the *highest-severity* priority present
    among the group's actions (high > medium > low), not the most frequent
    one — a single high-priority action in an otherwise-medium group should
    still surface the group as high-priority. Defaults to "medium" if no
    action carries a recognized priority.
    """
    best = None
    best_rank = -1
    for action in actions:
        priority = action.get("priority")
        rank = _PRIORITY_RANK.get(priority, -1)
        if rank > best_rank:
            best_rank = rank
            best = priority
    return best or "medium"


def _group_summary(group_id: str, count: int) -> str:
    if group_id == "document_gaps":
        if count == 1:
            return "1 project is missing indexed project documents."
        return f"{count} projects are missing indexed project documents."
    template = _GROUP_SUMMARY_TEMPLATES.get(group_id, _GROUP_SUMMARY_TEMPLATES["other"])
    return template.format(count=count)


def build_action_groups(actions: list[dict]) -> list[dict]:
    """Group `recommended_actions` by source into deterministically ordered,
    frontend-ready groups. Backend returns the FULL action list per group —
    "top N visible" truncation is a frontend concern; `default_visible_count`
    is only a hint.

    Unknown/missing sources are merged into a single trailing "other" group.
    Groups with zero actions are omitted entirely.
    """
    buckets: dict[str, list[dict]] = {}
    for action in actions:
        source = action.get("source") or "other"
        group_id = source if source in _GROUP_TITLES else "other"
        buckets.setdefault(group_id, []).append(action)

    groups: list[dict] = []
    for group_id in (*_GROUP_ORDER, "other"):
        group_actions = buckets.get(group_id)
        if not group_actions:
            continue
        count = len(group_actions)
        groups.append({
            "id": group_id,
            "title": _GROUP_TITLES[group_id],
            "source": group_id,
            "count": count,
            "priority": _group_priority(group_actions),
            "summary": _group_summary(group_id, count),
            "default_visible_count": count if count <= _DEFAULT_VISIBLE_LIMIT else _DEFAULT_VISIBLE_LIMIT,
            "actions": group_actions,
        })
    return groups
