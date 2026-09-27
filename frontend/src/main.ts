import { createRoot } from 'react-dom/client';
import { createElement, StrictMode } from 'react';
import { CiteRagApp } from './react/App';

const root = document.querySelector<HTMLElement>('#app');
if (root) createRoot(root).render(createElement(StrictMode, null, createElement(CiteRagApp)));
