export function setupSidebarResize(main: HTMLElement) {
  const widths = {left: 248, right: 340};
  const minimum = {left: 190, right: 260};
  const handles = {left: document.getElementById('resize-left')!, right: document.getElementById('resize-right')!};
  const hasRight = () => main.classList.contains('has-tool-panel');
  function update(side?: 'left' | 'right', value?: number) {
    if (main.clientWidth <= 800) return;
    if (side && value !== undefined) widths[side] = Math.max(minimum[side], Math.min(560, value));
    const budget = main.clientWidth - 292;
    if (hasRight()) {
      const overflow = widths.left + widths.right - budget;
      if (overflow > 0) {
        const first = side || 'right';
        const other = first === 'left' ? 'right' : 'left';
        const reduction = Math.min(overflow, widths[first] - minimum[first]);
        widths[first] -= reduction;
        widths[other] = Math.max(minimum[other], widths[other] - overflow + reduction);
      }
    } else widths.left = Math.min(widths.left, budget);
    for (const key of ['left', 'right'] as const) {
      main.style.setProperty(`--${key}-width`, `${widths[key]}px`);
      handles[key].setAttribute('aria-valuemin', String(minimum[key]));
      handles[key].setAttribute('aria-valuemax', '560');
      handles[key].setAttribute('aria-valuenow', String(Math.round(widths[key])));
    }
  }
  for (const side of ['left', 'right'] as const) {
    const handle = handles[side];
    let drag: {x: number; width: number} | null = null;
    handle.onpointerdown = event => {
      if (event.button !== 0) return;
      drag = {x: event.clientX, width: widths[side]};
      handle.setPointerCapture(event.pointerId);
      main.classList.add('resizing');
      event.preventDefault();
    };
    handle.onpointermove = event => {
      if (drag) update(side, drag.width + (event.clientX - drag.x) * (side === 'left' ? 1 : -1));
    };
    handle.onlostpointercapture = () => {drag = null; main.classList.remove('resizing');};
    handle.onpointerup = event => {if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);};
    handle.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      update(side, widths[side] + (event.key === 'ArrowRight' ? 16 : -16) * (side === 'left' ? 1 : -1));
    };
  }
  new ResizeObserver(() => update()).observe(main);
  new MutationObserver(() => update()).observe(main, {attributes: true, attributeFilter: ['class']});
  update();
}
