"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { useI18n } from "@/lib/i18n";
import type { MapPoint, RouteView } from "@/lib/types";

interface Area {
  id: string;
  floor: string;
  concourse: string;
  terminal: string;
  paths: MapPoint[][];
  outline?: MapPoint[];
  markers: { point: MapPoint; numbers: number[]; caption: string }[];
}

function routeAreas(route: RouteView, caption: (step: RouteView["steps"][number]) => string): Area[] {
  const areas = new Map<string, Area>();
  const add = (terminal: string, concourse: string, floor: string) => {
    const id = [terminal, concourse, floor].join("/");
    const area: Area = areas.get(id) ?? { id, floor, concourse, terminal, paths: [], markers: [] };
    areas.set(id, area);
    return area;
  };
  // Steps put the levels in traversal order, including levels with only a
  // lift or train arrival. Assembling paths separately never connects floors.
  for (const step of route.steps) {
    if (!step.map_position) continue;
    const point = step.map_position;
    const area = add(point.terminal, point.concourse, step.floor);
    const marker = area.markers.find((marker) => marker.point.x === point.x && marker.point.y === point.y);
    if (marker) marker.numbers.push(step.number);
    else area.markers.push({ point, numbers: [step.number], caption: caption(step) });
  }
  for (const shape of route.floor_shapes) {
    add(shape.terminal, shape.concourse, shape.floor).paths.push(shape.points);
  }
  for (const shape of route.floor_outlines ?? []) {
    const area = areas.get([shape.terminal, shape.concourse, shape.floor].join("/"));
    if (area) area.outline = shape.points;
  }
  return [...areas.values()];
}

function drawRoute(canvas: HTMLCanvasElement, area: Area) {
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas.getBoundingClientRect();
  const points = [...(area.outline ?? []), ...area.paths.flat(), ...area.markers.map((marker) => marker.point)];
  if (!ctx || !width || !height || !points.length) return;
  const dpi = Math.min(2, window.devicePixelRatio);
  canvas.width = Math.round(width * dpi);
  canvas.height = Math.round(height * dpi);
  ctx.setTransform(dpi, 0, 0, dpi, 0, 0);
  const style = getComputedStyle(canvas);
  const ink = style.getPropertyValue("--workspace-route-ink").trim();
  const paper = style.getPropertyValue("--workspace-route-text").trim();
  const grid = style.getPropertyValue("--workspace-rule").trim();
  const surface = style.getPropertyValue("--workspace-surface").trim();
  const text = style.color;
  const x = points.map((p) => p.x);
  const y = points.map((p) => p.y);
  const middleX = (Math.min(...x) + Math.max(...x)) / 2;
  const middleY = (Math.min(...y) + Math.max(...y)) / 2;
  const unit = Math.min((width - 72) / Math.max(22, Math.max(...x) - Math.min(...x)),
    (height - 80) / Math.max(20, Math.max(...y) - Math.min(...y)));
  const screen = (p: MapPoint): [number, number] => [width / 2 + (p.x - middleX) * unit, height / 2 + (p.y - middleY) * unit];

  ctx.fillStyle = grid;
  for (let dx = 16; dx < width; dx += 16) {
    for (let dy = 16; dy < height; dy += 16) ctx.fillRect(dx, dy, 1.5, 1.5);
  }
  if (area.outline && area.outline.length > 2) {
    ctx.beginPath();
    ctx.moveTo(...screen(area.outline[0]));
    area.outline.slice(1).forEach((point) => ctx.lineTo(...screen(point)));
    ctx.closePath();
    ctx.fillStyle = surface;
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = grid;
    ctx.stroke();
  }
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = ink;
  ctx.lineWidth = 4;
  for (const path of area.paths) {
    if (path.length < 2) continue;
    ctx.beginPath();
    ctx.moveTo(...screen(path[0]));
    path.slice(1).forEach((point) => ctx.lineTo(...screen(point)));
    ctx.stroke();
    for (const point of [path[0], path[path.length - 1]]) {
      ctx.beginPath();
      ctx.arc(...screen(point), 5, 0, 2 * Math.PI);
      ctx.fillStyle = paper;
      ctx.fill();
      ctx.stroke();
    }
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = "600 13px system-ui";
  for (const [index, { point, numbers, caption }] of area.markers.entries()) {
    const [px, py] = screen(point);
    const label = numbers.join("·");
    const radius = Math.max(13, ctx.measureText(label).width / 2 + 6);
    ctx.beginPath();
    ctx.arc(px, py, radius, 0, 2 * Math.PI);
    ctx.fillStyle = ink;
    ctx.fill();
    ctx.fillStyle = paper;
    ctx.fillText(label, px, py);
    if (area.markers.length <= 2) {
      const maxTextWidth = area.markers.length === 1 ? width - 24 : (width - 32) / 2;
      const textWidth = Math.min(ctx.measureText(caption).width, maxTextWidth);
      const captionX = Math.max(12 + textWidth / 2, Math.min(px, width - 12 - textWidth / 2));
      ctx.fillStyle = text;
      const captionOffset = index === 0 ? radius + 12 : -radius - 12;
      ctx.fillText(caption, captionX, py + captionOffset, maxTextWidth);
    }
  }
}

function RouteDiagram({ area, label }: { area: Area; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => drawRoute(element, area);
    const size = new ResizeObserver(draw);
    const theme = new MutationObserver(draw);
    size.observe(element);
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => { size.disconnect(); theme.disconnect(); };
  }, [area]);
  return <canvas ref={canvas} className="route-diagram" role="img" aria-label={label} />;
}

export function RouteMap({ route }: { route: RouteView }) {
  const { t, r } = useI18n();
  const areas = useMemo(() => routeAreas(route, (step) => r(step.landmark)), [route, r]);
  const [selection, setSelection] = useState("");
  const area = areas.find((value) => value.id === selection) ?? areas[0];
  if (!area) return null;
  const label = ({ concourse, terminal, floor }: Area) => t(concourse && concourse !== "P"
    ? "maps.route_floor" : "maps.route_floor_terminal", { concourse, terminal, floor });
  return (
    <div className="flex min-w-0 flex-col items-start gap-3">
      {areas.length > 1 && (
        <SegmentedControl aria-label={t("maps.route_floor_group")} className="route-levels"
          selectedKeys={[area.id]} onSelectionChange={(keys) => setSelection(String([...keys][0] ?? ""))}>
          {areas.map((item) => <SegmentedControlItem key={item.id} id={item.id}>{label(item)}</SegmentedControlItem>)}
        </SegmentedControl>
      )}
      <RouteDiagram area={area} label={t("maps.route_on_level", { area: label(area) })} />
    </div>
  );
}
