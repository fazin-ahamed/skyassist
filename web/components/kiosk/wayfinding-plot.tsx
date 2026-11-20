"use client";

import { useEffect, useRef } from "react";

type Point = [number, number];
type Segment = [Point, Point];

const WIDTH = 720;
const HEIGHT = 380;
const PIERS: Segment[] = [
  [[30, 311], [690, 311]],
  [[344, 309], [240, 229]],
  [[240, 229], [174, 229]],
  [[425, 309], [537, 228]],
  [[537, 228], [634, 228]],
];
const JET_BRIDGES: Segment[] = [
  ...Array.from({ length: 7 }, (_, i): Segment => [[77 + i * 41, 302], [77 + i * 41, 281]]),
  ...Array.from({ length: 7 }, (_, i): Segment => [[98 + i * 41, 318], [98 + i * 41, 339]]),
  ...Array.from({ length: 5 }, (_, i): Segment => [[467 + i * 41, 302], [467 + i * 41, 281]]),
  ...Array.from({ length: 5 }, (_, i): Segment => [[484 + i * 41, 318], [484 + i * 41, 339]]),
  [[193, 228], [193, 207]], [[217, 228], [217, 206]], [[256, 237], [256, 215]], [[286, 260], [286, 237]],
  [[515, 244], [515, 221]], [[551, 228], [551, 207]], [[584, 228], [584, 207]], [[614, 228], [614, 207]],
];

function near(x: number, y: number, [[ax, ay], [bx, by]]: Segment, distance: number) {
  const dx = bx - ax;
  const dy = by - ay;
  const along = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)));
  return (x - ax - along * dx) ** 2 + (y - ay - along * dy) ** 2 < distance ** 2;
}

function airplane() {
  const plane = new Path2D();
  const corners: Point[] = [
    [0, -34], [4, -29], [5, -13], [31, 5], [31, 11], [5, 1], [4, 20],
    [12, 27], [12, 31], [0, 27], [-12, 31], [-12, 27], [-4, 20],
    [-5, 1], [-31, 11], [-31, 5], [-5, -13], [-4, -29],
  ];
  corners.forEach(([x, y], i) => {
    if (i === 0) plane.moveTo(x + 662, y + 66);
    else plane.lineTo(x + 662, y + 66);
  });
  plane.closePath();
  return plane;
}

function routePoint(t: number): Point {
  const a = 1 - t;
  return [
    a ** 3 * 384 + 3 * a ** 2 * t * 477 + 3 * a * t ** 2 * 408 + t ** 3 * 644,
    a ** 3 * 320 + 3 * a ** 2 * t * 301 + 3 * a * t ** 2 * 159 + t ** 3 * 104,
  ];
}

// Canvas dot field adapted to a terminal silhouette from the React Bits Dot Grid
// pattern (https://reactbits.dev/backgrounds/dot-grid). It draws only on resize.
export function WayfindingPlot() {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) return;
    const plane = airplane();

    const draw = () => {
      const { width, height } = element.getBoundingClientRect();
      if (!width || !height) return;
      const scale = Math.min(window.devicePixelRatio, 2);
      element.width = Math.round(width * scale);
      element.height = Math.round(height * scale);
      const style = getComputedStyle(element);
      const dots = style.getPropertyValue("--workspace-map").trim();
      const active = style.getPropertyValue("--workspace-signal").trim();
      const surface = style.getPropertyValue("--workspace-stage").trim();
      const sx = element.width / WIDTH;
      const sy = element.height / HEIGHT;
      context.setTransform(sx, 0, 0, sy, 0, 0);

      const point = (x: number, y: number, radius: number, color: string) => {
        context.fillStyle = color;
        context.beginPath();
        context.arc(x, y, radius, 0, 2 * Math.PI);
        context.fill();
      };

      for (let x = 30; x < WIDTH - 16; x += 5) {
        for (let y = 32; y < HEIGHT - 24; y += 5) {
          const terminal = (x > 338 && x < 429 && y > 283 && y < 341) || PIERS.some((pier) => near(x, y, pier, 10));
          const bridge = JET_BRIDGES.some((pier) => near(x, y, pier, 4));
          // Path hit testing expects canvas coordinates after its transform.
          const planeDot = context.isPointInPath(plane, x * sx, y * sy);
          if (terminal || bridge || planeDot) {
            context.globalAlpha = planeDot ? 0.98 : 0.51 + (Math.sin(x * 0.19 + y * 0.43) + 1) * 0.21;
            point(x, y, planeDot ? 1.6 : 1.5, planeDot ? active : dots);
          }
        }
      }

      context.globalAlpha = 1;
      for (let i = 0; i <= 36; i++) {
        const [x, y] = routePoint(i / 36);
        point(x, y, i % 12 === 0 ? 5.25 : 2.2, active);
        if (i % 12 === 0) point(x, y, 2.55, i === 0 ? surface : dots);
      }
    };

    const observer = new ResizeObserver(draw);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return <canvas ref={canvas} className="wayfinding-plot" aria-hidden="true" />;
}
