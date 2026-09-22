export function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text?: string): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

export function action(label: string, className = 'button secondary', onClick?: () => void): HTMLButtonElement {
  const element = el('button', className, label);
  element.type = 'button';
  if (onClick) element.addEventListener('click', onClick);
  return element;
}

const paths = {
  plus: ['M10 4v12M4 10h12'],
  search: ['M8.5 14a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11ZM13 13l4 4'],
  book: ['M10 4v13M10 4C7 2 3 3 3 3v13s4-1 7 1c3-2 7-1 7-1V3s-4-1-7 1Z'],
  lock: ['M6 9h8v8H6zM8 9V6a2 2 0 0 1 4 0v3M10 12v2'],
  image: ['M3 3h14v14H3zM3 13l4-4 3 3 2-2 5 5M12 6h.01'],
  mic: ['M7 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0ZM4 9v2a6 6 0 0 0 12 0V9M10 17v2M7 19h6'],
  arrow: ['M10 16V4M5 9l5-5 5 5'],
  folder: ['M2 5h6l2 2h8v10H2z'],
  status: ['M3 11h3l2-5 4 9 2-5h3'],
  back: ['M16 10H4M9 5l-5 5 5 5'],
  upload: ['M10 13V3M6 7l4-4 4 4M3 13v4h14v-4'],
  refresh: ['M16 7a7 7 0 0 0-12-1M4 3v4h4M4 13a7 7 0 0 0 12 1M16 17v-4h-4'],
};

export function icon(name: keyof typeof paths): SVGSVGElement {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.5');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  for (const d of paths[name]) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

export function alert(message: string, kind = 'error') {
  const block = el('div', `notice ${kind}`, message);
  block.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  return block;
}

export function heading(text: string) {
  const title = el('h1', '', text);
  title.tabIndex = -1;
  return title;
}
