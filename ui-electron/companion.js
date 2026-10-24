'use strict';

const SHORTCUT = 'CommandOrControl+Alt+Shift+E';

function fitBounds(bounds, area, center = false) {
  const width = Math.min(400, Math.max(300, Math.round(bounds?.width || 400)), area.width);
  const height = Math.min(640, Math.max(420, Math.round(bounds?.height || 640)), area.height);
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

function trayImage(image, platform) {
  if (image.isEmpty() || platform !== 'darwin') return image;
  const size = image.getSize();
  return image.resize({ width: Math.max(1, Math.round(size.width * 2 / 3)),
    height: Math.max(1, Math.round(size.height * 2 / 3)), quality: 'best' });
}

module.exports = { SHORTCUT, fitBounds, fitLauncher, point, dragBounds, trayImage };
