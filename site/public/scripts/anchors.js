// Links to a section (/#sync, /sync/#meeting-recordings) land on it.
//
// The browser jumps to the section while the page is still in the fallback
// font; the web font arrives after the load event and every block above the
// section changes height, which leaves the section off screen. Once fonts and
// images have loaded, land on it again, unless the visitor has already
// scrolled, tapped or pressed a key. Smooth scrolling for links within the
// page starts after that (html.smooth-scroll in global.css), so the first
// landing is not animated through a page that is still changing.
(() => {
  const root = document.documentElement;
  const inputs = ['wheel', 'touchmove', 'keydown', 'pointerdown'];
  let interacted = false;
  const stop = () => { interacted = true; };
  for (const type of inputs) addEventListener(type, stop, { passive: true, capture: true });

  const target = () => {
    if (location.hash.length < 2) return null;
    try {
      return document.getElementById(decodeURIComponent(location.hash.slice(1)));
    } catch {
      return null;
    }
  };
  const land = () => {
    if (!interacted) target()?.scrollIntoView({ block: 'start' });
  };

  addEventListener('load', () => {
    land();
    const fonts = document.fonts ? document.fonts.ready : Promise.resolve();
    fonts.then(() => {
      land();
      // A late layout pass (images decoding, the relay status) can still move it.
      requestAnimationFrame(() => requestAnimationFrame(land));
      for (const type of inputs) removeEventListener(type, stop, { capture: true });
      root.classList.add('smooth-scroll');
    });
  });
})();
