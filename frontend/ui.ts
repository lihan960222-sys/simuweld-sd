const paths = {
  add: '<path d="M12 5.25v13.5M5.25 12h13.5"/>',
  save: '<path d="M5.5 3.5h10.8l3.7 3.7v11.3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 1.5-2ZM8 3.5v5h8v-5M8 20v-7h8v7"/>',
  edit: '<path d="m15.3 5.3 3.4 3.4M5 19l3.9-.8L19 8.1a2.1 2.1 0 0 0-3-3L5.8 15.2 5 19Z"/>',
  delete: '<path d="M5 7h14M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M7 7l.7 11.5a1.6 1.6 0 0 0 1.6 1.5h5.4a1.6 1.6 0 0 0 1.6-1.5L17 7M10 10.5v5.5m4-5.5v5.5"/>',
  close: '<path d="m7 7 10 10M17 7 7 17"/>',
  play: '<path d="M8.5 5.8a1 1 0 0 1 1.5-.9l9.2 6.2a1 1 0 0 1 0 1.8L10 19.1a1 1 0 0 1-1.5-.9V5.8Z"/>',
  pause: '<path d="M8.5 5.5v13m7-13v13" stroke-width="2.3"/>',
  reset: '<path d="M4.8 8.8A7.7 7.7 0 1 1 4.4 15M4.8 4.7v4.1h4.1"/>',
  reverse: '<path d="M4.5 8h14.8l-3.2-3.2M19.5 16H4.7l3.2 3.2"/>',
  start: '<circle cx="6.2" cy="12" r="2.7"/><path d="M9.5 12h10m-3.2-3.2 3.2 3.2-3.2 3.2"/>',
  end: '<circle cx="17.8" cy="12" r="2.7"/><path d="M4.5 12h10m-3.2-3.2 3.2 3.2-3.2 3.2"/>',
  path: '<circle cx="5.5" cy="6" r="2"/><circle cx="18.5" cy="18" r="2"/><path d="M7.5 6h8a3.5 3.5 0 0 1 0 7h-7a2.5 2.5 0 0 0 0 5h8"/>',
  grip: '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" stroke-width="3"/>',
  all: '<rect x="4" y="4" width="6" height="6" rx="1.4"/><rect x="14" y="4" width="6" height="6" rx="1.4"/><rect x="4" y="14" width="6" height="6" rx="1.4"/><rect x="14" y="14" width="6" height="6" rx="1.4"/>',
  eye: '<path d="M3 12s3.3-6 9-6 9 6 9 6-3.3 6-9 6-9-6-9-6Z"/><circle cx="12" cy="12" r="2.6"/>',
  upload: '<path d="M12 15V4.5m-3.8 3.8L12 4.5l3.8 3.8M4.5 15v2.5A2.5 2.5 0 0 0 7 20h10a2.5 2.5 0 0 0 2.5-2.5V15"/>',
  download: '<path d="M12 4.5V15m-3.8-3.8L12 15l3.8-3.8M4.5 15v2.5A2.5 2.5 0 0 0 7 20h10a2.5 2.5 0 0 0 2.5-2.5V15"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 10.5v5.5m0-8v.1" stroke-width="2"/>',
  explode: '<path d="m12 8.8 3.2 3.2-3.2 3.2L8.8 12 12 8.8ZM4 8V4h4M4 4l4.1 4.1M16 4h4v4m0-4-4.1 4.1M4 16v4h4m-4 0 4.1-4.1M16 20h4v-4m0 4-4.1-4.1"/>',
};
export type IconName = keyof typeof paths;
export function setIcon(button: HTMLElement, name: IconName, label: string) {
  button.innerHTML=`<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${paths[name]}</svg>`;
  button.classList.add('icon-button');button.setAttribute('aria-label',label);button.title=label;
}
export function iconButton(name: IconName, label: string) {
  const button=document.createElement('button');setIcon(button,name,label);return button;
}
export function makeCollapsible() {
  document.querySelectorAll<HTMLElement>('aside > section').forEach(section=>{
    const heading=section.querySelector<HTMLElement>(':scope > .section-title, :scope > h2');
    if(!heading) return;
    const details=document.createElement('details');details.className='panel-group';details.open=true;
    const summary=document.createElement('summary'),content=document.createElement('div');content.className='panel-content';
    summary.append(heading);content.append(...Array.from(section.childNodes));details.append(summary,content);section.append(details);
    summary.addEventListener('click',event=>{if((event.target as Element).closest('button,input,select')) event.preventDefault();});
  });
}
