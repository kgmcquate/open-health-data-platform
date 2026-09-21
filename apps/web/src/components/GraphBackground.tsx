import { useEffect, useRef } from "react";
import graphData from "../data/graph-data.json";

type GraphNode = { id: string; x: number; y: number; z: number; group: number };
type GraphEdge = { source: string; target: string };

const { nodes, edges } = graphData as { nodes: GraphNode[]; edges: GraphEdge[] };

const nodeIndex = new Map(nodes.map((n, i) => [n.id, i]));
const edgePairs = edges
  .map((e) => [nodeIndex.get(e.source), nodeIndex.get(e.target)] as const)
  .filter((pair): pair is [number, number] => pair[0] !== undefined && pair[1] !== undefined);

// Ambient rotation speed in radians/sec around each axis, and how a drag maps to it.
const SPEED_Y = 0.08;
const SPEED_X = 0.035;
const FOV = 2.2;
const RADIUS_FACTOR = 0.92;
const DRAG_SENSITIVITY = 0.006; // radians rotated per pixel dragged
const MAX_FLING_VELOCITY = 4; // rad/sec cap after a fast drag release
const VELOCITY_RELAX_RATE = 1.5; // how quickly fling velocity settles back to ambient speed

const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

/** Rotating 3D point-and-edge graph rendered on canvas; drag to spin it manually. */
export default function GraphBackground({ className = "" }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const container = canvas.parentElement;
    if (!container) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const colors = { edge: "", node: "" };
    const readColors = () => {
      const style = getComputedStyle(document.documentElement);
      colors.edge = style.getPropertyValue("--color-primary").trim();
      colors.node = style.getPropertyValue("--color-secondary").trim();
    };
    readColors();

    // The navbar theme toggle flips `data-theme` without a page reload.
    const themeObserver = new MutationObserver(readColors);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const withAlpha = (color: string, alpha: number) =>
      color ? color.replace(/\)\s*$/, ` / ${alpha})`) : `rgba(120,120,120,${alpha})`;

    let width = 0;
    let height = 0;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      width = container.clientWidth;
      height = container.clientHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    };
    resize();

    const ro = new ResizeObserver(resize);
    ro.observe(container);

    let angleY = 0;
    let angleX = 0;
    let velocityY = reduceMotion ? 0 : SPEED_Y;
    let velocityX = reduceMotion ? 0 : SPEED_X;

    let isDragging = false;
    let lastPointerX = 0;
    let lastPointerY = 0;
    let lastMoveTime = 0;
    let dragVelocityY = 0;
    let dragVelocityX = 0;

    const onPointerDown = (e: PointerEvent) => {
      isDragging = true;
      lastPointerX = e.clientX;
      lastPointerY = e.clientY;
      lastMoveTime = performance.now();
      dragVelocityY = 0;
      dragVelocityX = 0;
      canvas.setPointerCapture(e.pointerId);
    };
    const onPointerMove = (e: PointerEvent) => {
      if (!isDragging) return;
      const now = performance.now();
      const dt = Math.max(1, now - lastMoveTime) / 1000;
      const dx = e.clientX - lastPointerX;
      const dy = e.clientY - lastPointerY;
      lastPointerX = e.clientX;
      lastPointerY = e.clientY;
      lastMoveTime = now;

      angleY += dx * DRAG_SENSITIVITY;
      angleX += dy * DRAG_SENSITIVITY;

      const instVelY = (dx * DRAG_SENSITIVITY) / dt;
      const instVelX = (dy * DRAG_SENSITIVITY) / dt;
      dragVelocityY = dragVelocityY * 0.7 + instVelY * 0.3;
      dragVelocityX = dragVelocityX * 0.7 + instVelX * 0.3;
    };
    const onPointerUp = (e: PointerEvent) => {
      if (!isDragging) return;
      isDragging = false;
      velocityY = clamp(dragVelocityY, -MAX_FLING_VELOCITY, MAX_FLING_VELOCITY);
      velocityX = clamp(dragVelocityX, -MAX_FLING_VELOCITY, MAX_FLING_VELOCITY);
      canvas.releasePointerCapture(e.pointerId);
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);

    let lastTime = performance.now();
    let frameId = 0;

    const projected = new Array(nodes.length).fill(null) as (
      | { x: number; y: number; scale: number; z: number }
      | null
    )[];

    const render = (time: number) => {
      const dt = (time - lastTime) / 1000;
      lastTime = time;

      if (isDragging) {
        // Angle is driven directly by pointer move while dragging.
      } else {
        angleY += velocityY * dt;
        angleX += velocityX * dt;
        // Let a fling settle back toward the ambient speed (or a stop, under reduced motion).
        const relax = Math.min(1, dt * VELOCITY_RELAX_RATE);
        const targetY = reduceMotion ? 0 : SPEED_Y;
        const targetX = reduceMotion ? 0 : SPEED_X;
        velocityY += (targetY - velocityY) * relax;
        velocityX += (targetX - velocityX) * relax;
      }

      const cosY = Math.cos(angleY);
      const sinY = Math.sin(angleY);
      const cosX = Math.cos(angleX);
      const sinX = Math.sin(angleX);

      const radius = Math.min(width, height) * RADIUS_FACTOR;
      const cx = width / 2;
      const cy = height / 2;

      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        // Rotate around Y, then around X.
        const x1 = n.x * cosY - n.z * sinY;
        const z1 = n.x * sinY + n.z * cosY;
        const y2 = n.y * cosX - z1 * sinX;
        const z2 = n.y * sinX + z1 * cosX;

        const scale = FOV / (FOV + z2);
        projected[i] = { x: cx + x1 * radius * scale, y: cy + y2 * radius * scale, scale, z: z2 };
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      ctx.lineWidth = 1;
      for (const [a, b] of edgePairs) {
        const pa = projected[a];
        const pb = projected[b];
        if (!pa || !pb) continue;
        const depth = (pa.scale + pb.scale) / 2;
        ctx.strokeStyle = withAlpha(colors.edge, Math.min(0.35, depth * 0.28));
        ctx.beginPath();
        ctx.moveTo(pa.x, pa.y);
        ctx.lineTo(pb.x, pb.y);
        ctx.stroke();
      }

      for (const p of projected) {
        if (!p) continue;
        ctx.fillStyle = withAlpha(colors.node, Math.min(0.9, 0.35 + p.scale * 0.5));
        ctx.beginPath();
        ctx.arc(p.x, p.y, Math.max(1, 2.4 * p.scale), 0, Math.PI * 2);
        ctx.fill();
      }

      frameId = requestAnimationFrame(render);
    };

    frameId = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(frameId);
      ro.disconnect();
      themeObserver.disconnect();
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerUp);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={`absolute inset-0 cursor-grab touch-none active:cursor-grabbing ${className}`}
    />
  );
}
