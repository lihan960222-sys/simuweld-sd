import { createElement, Plus, Save, Pencil, Trash2, X, Play, Pause, RotateCcw, ArrowLeftRight, MapPin, MapPinCheck, Route, GripVertical, Layers3, ListVideo, Eye, Upload, Download, Info, Boxes, Focus, SlidersHorizontal } from 'lucide';

const icons = {
  add: Plus, save: Save, edit: Pencil, delete: Trash2, close: X,
  play: Play, pause: Pause, reset: RotateCcw, reverse: ArrowLeftRight,
  start: MapPin, end: MapPinCheck, path: Route, grip: GripVertical,
  all: Layers3, playAll: ListVideo, eye: Eye, upload: Upload, download: Download,
  info: Info, explode: Boxes, focus: Focus, settings: SlidersHorizontal,
};
export type IconName = keyof typeof icons;
export function setIcon(button: HTMLElement, name: IconName, label: string) {
  const svg=createElement(icons[name]);svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
  button.replaceChildren(svg);
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
