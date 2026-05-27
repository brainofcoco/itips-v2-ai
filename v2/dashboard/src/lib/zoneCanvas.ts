// Shared canvas helpers for drawing zones (regions + lines).
// Used by both the Zones editor and the read-only overlay on Live.
import type { Zone, ZoneDirection, ZoneType } from "../api/types";

export type Point = [number, number];

export const ZONE_COLORS = [
  "#7c3aed", "#22c55e", "#f59e0b", "#ef4444",
  "#14b8a6", "#ec4899", "#3b82f6", "#a855f7",
];

export function colorForZone(idx: number): string {
  return ZONE_COLORS[idx % ZONE_COLORS.length];
}

export function pxFromNorm([nx, ny]: Point, w: number, h: number): Point {
  return [nx * w, ny * h];
}

export function normFromPx(px: number, py: number, w: number, h: number): Point {
  return [px / w, py / h];
}

interface DrawZoneOpts {
  color: string;
  width?: number;
  fillAlpha?: number;
  label?: string;
  showHandles?: boolean;
}

export function drawZone(
  g: CanvasRenderingContext2D,
  zone: Zone,
  w: number, h: number,
  opts: DrawZoneOpts,
) {
  const pts = zone.points.map((p) => pxFromNorm(p as Point, w, h));
  if (pts.length === 0) return;
  const color = opts.color;
  const lineWidth = opts.width ?? 2;
  if (zone.zone_type === "region") {
    drawPolygon(g, pts, {
      stroke: color,
      fill: color + hexAlpha(opts.fillAlpha ?? 0.18),
      close: true,
      width: lineWidth,
    });
  } else {
    drawPolyline(g, pts, { stroke: color, width: Math.max(2, lineWidth + 1) });
    if (zone.direction && zone.direction !== "Any" && pts.length >= 2) {
      drawDirectionArrow(g, pts, color, zone.direction);
    }
  }
  if (opts.showHandles) {
    drawHandles(g, pts, color);
  }
  if (opts.label) {
    drawLabel(g, pts[0][0], pts[0][1], opts.label, color);
  }
}

export function drawInProgress(
  g: CanvasRenderingContext2D,
  drawing: Point[],
  w: number, h: number,
  zoneType: ZoneType,
  direction: ZoneDirection,
) {
  if (drawing.length === 0) return;
  const pts = drawing.map((p) => pxFromNorm(p, w, h));
  const wantRegion = zoneType === "region";
  if (wantRegion && pts.length >= 3) {
    drawPolygon(g, pts, { stroke: "#ffffff", fill: "#ffffff22", close: true, width: 2, dashed: true });
  } else {
    drawPolyline(g, pts, { stroke: "#ffffff", width: 2, dashed: true });
    if (!wantRegion && direction !== "Any" && pts.length >= 2) {
      drawDirectionArrow(g, pts, "#ffffff", direction);
    }
  }
  pts.forEach(([x, y], i) => {
    g.fillStyle = i === 0 ? "#22d3ee" : "#ffffff";
    g.beginPath();
    g.arc(x, y, 5, 0, Math.PI * 2);
    g.fill();
    g.lineWidth = 1.5;
    g.strokeStyle = "#0b0e13";
    g.stroke();
  });
}

export function drawHandles(g: CanvasRenderingContext2D, pts: Point[], color: string) {
  pts.forEach(([x, y]) => {
    g.beginPath();
    g.arc(x, y, 6, 0, Math.PI * 2);
    g.fillStyle = "#0b0e13";
    g.fill();
    g.lineWidth = 2;
    g.strokeStyle = color;
    g.stroke();
  });
}

export function drawLabel(
  g: CanvasRenderingContext2D, x: number, y: number, text: string, color: string,
) {
  g.font = "600 12px ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif";
  const padX = 6, padY = 3;
  const metrics = g.measureText(text);
  const w = metrics.width + padX * 2;
  const h = 18;
  const lx = x + 6;
  const ly = Math.max(h + 2, y - 6);
  g.fillStyle = "rgba(0,0,0,0.65)";
  roundRect(g, lx, ly - h, w, h, 4);
  g.fill();
  g.lineWidth = 1;
  g.strokeStyle = color;
  g.stroke();
  g.fillStyle = "#ffffff";
  g.textBaseline = "alphabetic";
  g.fillText(text, lx + padX, ly - padY - 2);
}

function drawDirectionArrow(
  g: CanvasRenderingContext2D, pts: Point[], color: string, direction: ZoneDirection,
) {
  // Arrow perpendicular to the middle segment, pointing in the "allowed" cross direction.
  const i = Math.floor((pts.length - 1) / 2);
  const [ax, ay] = pts[i];
  const [bx, by] = pts[i + 1];
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const dx = bx - ax;
  const dy = by - ay;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  const sign = direction === "LeftToRight" ? 1 : -1;
  const tip = [mx + nx * 22 * sign, my + ny * 22 * sign];
  const tail = [mx - nx * 4 * sign, my - ny * 4 * sign];
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(tail[0], tail[1]);
  g.lineTo(tip[0], tip[1]);
  g.stroke();
  // arrowhead
  const ang = Math.atan2(tip[1] - tail[1], tip[0] - tail[0]);
  const head = 8;
  g.beginPath();
  g.moveTo(tip[0], tip[1]);
  g.lineTo(tip[0] - head * Math.cos(ang - Math.PI / 6), tip[1] - head * Math.sin(ang - Math.PI / 6));
  g.lineTo(tip[0] - head * Math.cos(ang + Math.PI / 6), tip[1] - head * Math.sin(ang + Math.PI / 6));
  g.closePath();
  g.fill();
}

interface PolyOpts {
  stroke: string;
  fill?: string;
  width?: number;
  dashed?: boolean;
  close?: boolean;
}

function drawPolygon(g: CanvasRenderingContext2D, pts: Point[], opts: PolyOpts) {
  g.lineWidth = opts.width ?? 2;
  g.strokeStyle = opts.stroke;
  g.setLineDash(opts.dashed ? [6, 4] : []);
  g.beginPath();
  pts.forEach(([x, y], i) => (i === 0 ? g.moveTo(x, y) : g.lineTo(x, y)));
  if (opts.close) g.closePath();
  if (opts.fill) { g.fillStyle = opts.fill; g.fill(); }
  g.stroke();
  g.setLineDash([]);
}

function drawPolyline(g: CanvasRenderingContext2D, pts: Point[], opts: PolyOpts) {
  g.lineWidth = opts.width ?? 2;
  g.strokeStyle = opts.stroke;
  g.setLineDash(opts.dashed ? [6, 4] : []);
  g.beginPath();
  pts.forEach(([x, y], i) => (i === 0 ? g.moveTo(x, y) : g.lineTo(x, y)));
  g.stroke();
  g.setLineDash([]);
}

function roundRect(
  g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number,
) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

function hexAlpha(a: number): string {
  const v = Math.round(Math.max(0, Math.min(1, a)) * 255);
  return v.toString(16).padStart(2, "0");
}

// Hit testing helpers — used by the editor for dragging / inserting / deleting.

export const HANDLE_RADIUS_PX = 9;
export const SEGMENT_HIT_PX = 8;

export function findHandleAt(
  pts: Point[], x: number, y: number,
): number {
  for (let i = pts.length - 1; i >= 0; i--) {
    const [px, py] = pts[i];
    if (Math.hypot(px - x, py - y) <= HANDLE_RADIUS_PX) return i;
  }
  return -1;
}

export function findSegmentAt(
  pts: Point[], x: number, y: number, closed: boolean,
): { index: number; insertAt: number } | null {
  // Returns the insertion index for a new vertex on the closest segment under (x,y).
  const n = pts.length;
  const segCount = closed ? n : n - 1;
  let best: { index: number; insertAt: number; d: number } | null = null;
  for (let i = 0; i < segCount; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const d = pointToSegmentDistance(x, y, a[0], a[1], b[0], b[1]);
    if (d <= SEGMENT_HIT_PX && (best === null || d < best.d)) {
      best = { index: i, insertAt: i + 1, d };
    }
  }
  return best ? { index: best.index, insertAt: best.insertAt } : null;
}

function pointToSegmentDistance(
  px: number, py: number, ax: number, ay: number, bx: number, by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const qx = ax + t * dx;
  const qy = ay + t * dy;
  return Math.hypot(px - qx, py - qy);
}
