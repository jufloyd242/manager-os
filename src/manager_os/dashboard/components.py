"""Reusable UI components for the Manager OS dashboard."""

from __future__ import annotations

import streamlit as st


def metric_card(title: str, value: str | int, delta: str | None = None, icon: str = "📊"):
    """Display a metric card."""
    st.metric(label=title, value=value, delta=delta, icon=icon)


def status_chip(status: str, color: str = "gray"):
    """Display a status chip/badge."""
    color_map = {
        "critical": "red",
        "high": "orange",
        "medium": "yellow",
        "low": "gray",
        "open": "blue",
        "completed": "green",
        "stale": "gray",
        "not_mine": "gray",
        "active": "green",
        "prospective": "blue",
        "completed_deal": "gray",
        "archived": "gray",
        "unknown": "gray",
    }
    c = color_map.get(status.lower(), color)
    st.markdown(f"<span style='background-color: {c}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em;'>{status.upper()}</span>", unsafe_allow_html=True)


def source_badge(source: str):
    """Display a source/provenance badge."""
    source_map = {
        "deals_csv": "Deals CSV",
        "forecast": "Forecast",
        "google_drive": "Google Drive",
        "google_chat": "Google Chat",
        "calendar": "Calendar",
        "obsidian": "Obsidian",
        "computed": "Computed",
        "unknown": "Unknown",
    }
    label = source_map.get(source.lower(), source)
    st.markdown(f"<span style='background-color: #e0e0e0; color: #333; padding: 2px 6px; border-radius: 4px; font-size: 0.75em; font-family: monospace;'>{label}</span>", unsafe_allow_html=True)


def link_button(label: str, url: str):
    """Display a clickable link button."""
    if url:
        st.markdown(f"[{label}]({url})", unsafe_allow_html=True)
    else:
        st.markdown(f"<span style='color: gray;'>{label} (N/A)</span>", unsafe_allow_html=True)


def entity_header(title: str, subtitle: str | None = None, badges: list[str] | None = None):
    """Display an entity header with optional badges."""
    st.markdown(f"### {title}")
    if subtitle:
        st.markdown(f"*{subtitle}*")
    if badges:
        cols = st.columns(len(badges))
        for i, badge in enumerate(badges):
            with cols[i]:
                source_badge(badge)


def detail_section(title: str):
    """Display a detail section header."""
    st.markdown(f"#### {title}")
    st.divider()


def empty_state(message: str):
    """Display an empty state message."""
    st.info(message)


def warning_card(message: str):
    """Display a warning card."""
    st.warning(message)


def provenance_badge(source: str, updated_at: str | None = None):
    """Display a provenance badge with optional update date."""
    text = source
    if updated_at:
        text += f" ({updated_at})"
    source_badge(text)
