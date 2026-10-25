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
function fitLauncher(bounds, area) {
  const width = Math.min(56, area.width), height = Math.min(56, area.height);
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

// The head docks beside the launcher, or above it when the work area is too narrow.
function dockCompanion(companion, launcher, area) {
  const icon = fitLauncher(launcher, area), gap = 10;
  let head = fitBounds(companion, area);
  if (head.width + icon.width + gap <= area.width) {
    const right = icon.x + icon.width + gap + head.width <= area.x + area.width;
    head = { ...head, x: right ? icon.x + icon.width + gap : icon.x - gap - head.width,
      y: icon.y + Math.round((icon.height - head.height) / 2) };
  } else {
    head = { ...head, height: Math.min(head.height, Math.max(1, area.height - icon.height - gap)),
      x: icon.x + Math.round((icon.width - head.width) / 2) };
    head.y = icon.y - gap - head.height;
  }
  return clampFloatingGroup(head, icon, area);
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

module.exports = { SHORTCUT, fitBounds, fitLauncher, point, dragBounds, clientPoint, trayImage, dockCompanion, clampFloatingGroup };
