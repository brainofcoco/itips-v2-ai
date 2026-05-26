"""Finalize-time enhancements: 30-s highlight clips + incident PDF.

Both are called from `EvidencePackager._handle_finalize` after pre/post
videos have been attached and before manifest signing. Both fail soft —
missing ffmpeg or reportlab logs a warning and the rest of the package
ships, satisfying everything the PRD strictly mandates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from itips.evidence.video_writer import transcode_or_copy

logger = logging.getLogger(__name__)


def build_highlight(post_video_path: Path, *, duration_s: float = 30.0) -> Optional[Path]:
    """First N seconds of the post-event video — that's the trigger moment.

    Operators want "what happened the instant the alarm fired" — the
    post buffer starts exactly there. Anything more clever (highest
    event density across pre+post, motion-vector analysis) would need
    a video index we don't have at this layer.
    """
    if not post_video_path.exists() or post_video_path.stat().st_size == 0:
        return None
    # Filename pattern matches the spec: Video_cam{N}_highlight.mp4
    out = post_video_path.with_name(
        post_video_path.stem.replace("_post", "_highlight") + ".mp4",
    )
    ok = transcode_or_copy(post_video_path, out, start_s=0.0,
                           duration_s=duration_s)
    if not ok:
        return None
    return out


def build_pdf_summary(
    *,
    package_dir: Path,
    metadata: dict[str, Any],
    event_log: list[dict[str, Any]],
    face_count: int,
    plate_count: int,
    sensor_count: int,
) -> Optional[Path]:
    """Single-page incident summary PDF — law enforcement readable.

    Returns the written path or None if reportlab isn't installed.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
        from reportlab.lib import colors
    except ImportError:
        logger.warning("reportlab not installed — skipping incident_summary.pdf")
        return None

    out = package_dir / "incident_summary.pdf"
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    flow = []
    flow.append(Paragraph("ITIPS Incident Evidence Summary", styles["Title"]))
    flow.append(Spacer(1, 12))

    header_rows = [
        ["Incident ID", metadata.get("incident_id", "")],
        ["Site ID", metadata.get("site_id", "")],
        ["Operator ID", metadata.get("operator_id", "")],
        ["Device ID", metadata.get("device_id", "")],
        ["Opened (UTC)", metadata.get("started_utc", "")],
        ["Finalized (UTC)", metadata.get("finalized_utc", "")],
        ["Status", metadata.get("status", "")],
        ["Closed reason", metadata.get("closed_reason", "")],
        ["Cameras active", ", ".join(str(c) for c in metadata.get("camera_ids_active", []))],
        ["Event count", str(metadata.get("event_count", 0))],
        ["Sensor events", str(sensor_count)],
        ["Face captures", str(face_count)],
        ["Plate captures", str(plate_count)],
    ]
    flow.append(_two_col_table(header_rows))
    flow.append(Spacer(1, 12))

    stage_log = metadata.get("alert_stage_log") or []
    if stage_log:
        flow.append(Paragraph("Alert stage timeline", styles["Heading2"]))
        rows = [["Stage", "Signal", "Timestamp (UTC)"]]
        for s in stage_log:
            rows.append([s.get("stage", ""), s.get("signal", ""), s.get("ts", "")])
        flow.append(_grid_table(rows))
        flow.append(Spacer(1, 12))

    interesting = [e for e in event_log if _is_interesting_event(e)]
    if interesting:
        flow.append(Paragraph("Notable events", styles["Heading2"]))
        rows = [["Kind", "Camera", "Detail", "Timestamp (UTC)"]]
        for e in interesting[:30]:
            rows.append([
                str(e.get("kind") or e.get("alert_type", "")),
                str(e.get("camera_id", "")),
                _short_detail(e),
                str(e.get("timestamp_utc", "")),
            ])
        flow.append(_grid_table(rows))
        flow.append(Spacer(1, 12))

    flow.append(Paragraph(
        "This package is sealed with HMAC-SHA-256 over a deterministic "
        "manifest. See manifest.json + signature.sha256 for verification.",
        styles["Italic"],
    ))
    doc.build(flow)
    return out


# ─── helpers ──────────────────────────────────────────────────────────


def _two_col_table(rows):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, TableStyle
    t = Table(rows, colWidths=[1.6 * inch, 4.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e8e8f0")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
    ]))
    return t


def _grid_table(rows):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    t = Table(rows, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d1d28")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


_INTERESTING_KINDS = {
    "stage_change", "face_intruder", "personnel_seen", "plate_capture",
    "sensor_alarm", "sensor_unverified", "sensor_intruder",
    "fire", "smoke", "face_capture", "behaviour",
}


def _is_interesting_event(event: dict[str, Any]) -> bool:
    kind = event.get("kind") or event.get("alert_type") or ""
    return str(kind) in _INTERESTING_KINDS


def _short_detail(event: dict[str, Any]) -> str:
    for key in ("ai_summary", "plate_number", "name", "summary", "reason", "rule_name"):
        v = event.get(key)
        if v:
            return str(v)[:60]
    return ""
