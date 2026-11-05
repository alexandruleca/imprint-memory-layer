'use strict';

const SHORTCUT = 'CommandOrControl+Alt+Shift+E';

function fitBounds(bounds, area, center = false) {
  const width = Math.min(400, Math.max(300, Math.round(Number.isFinite(bounds?.width) ? bounds.width : 400)), area.width);
  const height = Math.min(640, Math.max(420, Math.round(Number.isFinite(bounds?.height) ? bounds.height : 640)), area.height);
  const x = center ? area.x + (area.width - width) / 2 : Number.isFinite(bounds?.x) ? bounds.x : area.x + area.width - width - 20;
  const y = center ? area.y + (area.height - height) / 2 : Number.isFinite(bounds?.y) ? bounds.y : area.y + area.height - height - 20;
  return { x: Math.round(Math.max(area.x, Math.min(area.x + area.width - width, x))),
    y: Math.round(Math.max(area.y, Math.min(area.y + area.height - height, y))), width, height };
}

function point(value) {
  return value && Number.isFinite(value.x) && Number.isFinite(value.y)
    && Math.abs(value.x) <= 100000 && Math.abs(value.y) <= 100000;
}
// The orb's window: a 48 px orb with room for its status rim, focus ring and glow.
const ORB = 72;
function fitLauncher(bounds, area) {
  const width = Math.min(ORB, area.width), height = Math.min(ORB, area.height);
  return { width, height,
    x: Math.round(Math.max(area.x, Math.min(area.x + area.width - width, Number.isFinite(bounds?.x) ? bounds.x : area.x + 20))),
    y: Math.round(Math.max(area.y, Math.min(area.y + area.height - height, Number.isFinite(bounds?.y) ? bounds.y : area.y + (area.height - height) / 2))) };
}

function dragBounds(bounds, start, current) {
  if (!point(start) || !point(current)) return null;
  return { ...bounds, x: Math.round(bounds.x + current.x - start.x), y: Math.round(bounds.y + current.y - start.y) };
}

// Moves the launcher and head together so the pair stays inside one work area.
function clampFloatingGroup(companion, launcher, area) {
  const left = Math.min(companion.x, launcher.x), top = Math.min(companion.y, launcher.y);
  const right = Math.max(companion.x + companion.width, launcher.x + launcher.width);
  const bottom = Math.max(companion.y + companion.height, launcher.y + launcher.height);
  const dx = Math.max(area.x, Math.min(area.x + area.width - (right - left), left)) - left;
  const dy = Math.max(area.y, Math.min(area.y + area.height - (bottom - top), top)) - top;
  return { companion: { ...companion, x: companion.x + dx, y: companion.y + dy },
    launcher: { ...launcher, x: launcher.x + dx, y: launcher.y + dy } };
}

const clamp = (value, low, high) => Math.round(Math.max(low, Math.min(high, value)));

// Which side of the orb its actions open toward (1 right, -1 left) and how the
// fan tilts: level when there is room above and below, else toward the roomier
// side. A preferred side (where Echo is docked) holds while `need` px fit there.
const RADIUS = 68, SATELLITE = 40, VERTICAL_ROOM = 96;
function orbSide(launcher, area, prefer = 0, need = 0) {
  const right = area.x + area.width - (launcher.x + launcher.width), left = launcher.x - area.x;
  const side = prefer && (prefer > 0 ? right : left) >= need ? Math.sign(prefer) : right >= left ? 1 : -1;
  const cy = launcher.y + launcher.height / 2, above = cy - area.y, below = area.y + area.height - cy;
  return { side, tilt: above >= VERTICAL_ROOM && below >= VERTICAL_ROOM ? 'level' : below > above ? 'down' : 'up' };
}

const ANGLES = { level: [-45, 0, 45], down: [0, 45, 90], up: [-90, -45, 0] };
const LABEL = { width: 150, height: 26 }, CAPSULE = { width: 168, height: 32 };
// Where the actions window goes for the ring of Dashboard, Echo and Record, or
// for a panel of `size` beside the orb. `orb` is the orb's centre inside it.
function haloLayout(launcher, area, { side, tilt }, kind = 'ring', size = null) {
  const cx = launcher.x + launcher.width / 2, cy = launcher.y + launcher.height / 2;
  if (kind !== 'ring') {
    const width = Math.min(size?.width || 344, area.width), height = Math.min(size?.height || 402, area.height);
    // 12 px of the panel window is its shadow, so the panel itself sits 8 px from the orb's window.
    const x = side > 0 ? launcher.x + launcher.width - 4 : launcher.x + 4 - width;
    const below = area.y + area.height - launcher.y >= launcher.y + launcher.height - area.y;
    const bounds = { width, height, x: clamp(x, area.x, area.x + area.width - width),
      y: clamp(below ? launcher.y : launcher.y + launcher.height - height, area.y, area.y + area.height - height) };
    return { kind, side, tilt, bounds, orb: { x: Math.round(cx - bounds.x), y: Math.round(cy - bounds.y) },
      origin: `${side > 0 ? 'left' : 'right'} ${below ? 'top' : 'bottom'}` };
  }
  const boxes = [{ x: launcher.x, y: launcher.y, width: launcher.width, height: launcher.height }];
  const satellites = ANGLES[tilt].map((angle, index) => {
    const ux = Math.cos(angle * Math.PI / 180) * side, uy = Math.sin(angle * Math.PI / 180);
    const at = { x: cx + RADIUS * ux, y: cy + RADIUS * uy };
    const glow = SATELLITE / 2 + 10;
    boxes.push({ x: at.x - glow, y: at.y - glow, width: 2 * glow, height: 2 * glow });
    // Its name (or, for Record, the recording's capsule) goes beyond it, along its ray.
    const label = index === 2 ? CAPSULE : LABEL, anchor = { x: cx + (RADIUS + 28) * ux, y: cy + (RADIUS + 28) * uy };
    const slot = Math.abs(ux) >= 0.38
      ? { x: side > 0 ? anchor.x : anchor.x - label.width, y: anchor.y - label.height / 2, ...label }
      : { x: side > 0 ? at.x - SATELLITE / 2 : at.x + SATELLITE / 2 - label.width, y: uy > 0 ? anchor.y : anchor.y - label.height, ...label };
    boxes.push(slot);
    return { angle, at, slot };
  });
  const left = Math.max(area.x, Math.min(...boxes.map(b => b.x)) - 4), top = Math.max(area.y, Math.min(...boxes.map(b => b.y)) - 4);
  const right = Math.min(area.x + area.width, Math.max(...boxes.map(b => b.x + b.width)) + 4);
  const bottom = Math.min(area.y + area.height, Math.max(...boxes.map(b => b.y + b.height)) + 4);
  const bounds = { x: Math.round(left), y: Math.round(top), width: Math.round(right - left), height: Math.round(bottom - top) };
  const local = ({ x, y }) => ({ x: Math.round(x - bounds.x), y: Math.round(y - bounds.y) });
  return { kind, side, tilt, bounds, orb: local({ x: cx, y: cy }), radius: RADIUS,
    satellites: satellites.map(({ angle, at, slot }) => {
      const corner = local(slot);
      // A name that would leave the work area slides back inside it.
      const x = clamp(corner.x, 4 - (bounds.x - area.x), area.x + area.width - bounds.x - slot.width - 4);
      const y = clamp(corner.y, 4 - (bounds.y - area.y), area.y + area.height - bounds.y - slot.height - 4);
      return { angle, ...local(at), slot: { x, y, width: slot.width, height: slot.height } };
    }) };
}

// Echo docks beside the orb on its open side, centred on it and slid along it to
// stay on screen; the orb never moves for it. When neither side has room, Echo
// stacks above or below the orb.
function dockCompanion(companion, launcher, area, prefer = 0) {
  const icon = fitLauncher(launcher, area), gap = 2;
  const head = fitBounds(companion, area);
  const fits = side => side > 0 ? icon.x + icon.width + gap + head.width <= area.x + area.width : icon.x - gap - head.width >= area.x;
  const side = [Math.sign(prefer), 1, -1].find(side => side && fits(side));
  if (side) {
    return { launcher: icon, side, companion: { ...head, x: side > 0 ? icon.x + icon.width + gap : icon.x - gap - head.width,
      y: clamp(icon.y + (icon.height - head.height) / 2, area.y, area.y + area.height - head.height) } };
  }
  const above = icon.y - area.y >= area.y + area.height - (icon.y + icon.height);
  const height = Math.min(head.height, Math.max(1, (above ? icon.y - area.y : area.y + area.height - icon.y - icon.height) - gap));
  return { launcher: icon, side: 0, companion: { ...head, height, x: clamp(icon.x + (icon.width - head.width) / 2, area.x, area.x + area.width - head.width),
    y: above ? icon.y - gap - height : icon.y + icon.height + gap } };
}

// A screen point in a window's page coordinates (CSS pixels).
function clientPoint(cursor, content, zoom) {
  return { x: (cursor.x - content.x) / zoom, y: (cursor.y - content.y) / zoom };
}

function trayImage(image, platform) {
  if (image.isEmpty() || platform !== 'darwin') return image;
  const size = image.getSize();
  return image.resize({ width: Math.max(1, Math.round(size.width * 2 / 3)),
    height: Math.max(1, Math.round(size.height * 2 / 3)), quality: 'best' });
}

module.exports = { SHORTCUT, ORB, fitBounds, fitLauncher, point, dragBounds, clientPoint, trayImage, dockCompanion, clampFloatingGroup,
  orbSide, haloLayout };
