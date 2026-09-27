import { createRoot } from 'react-dom/client';
import { createElement, StrictMode } from 'react';
import { StandaloneVoice } from './react/App';
const root = document.querySelector<HTMLElement>('#voice-app');
if (root) createRoot(root).render(createElement(StrictMode, null, createElement(StandaloneVoice)));
