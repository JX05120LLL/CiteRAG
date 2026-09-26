import { mountApp } from './app';
import { createApi } from './api/client';
import './styles.css';
import './ui-upgrade.css';

const root = document.querySelector<HTMLElement>('#app');
if (root) void mountApp(root, createApi(), undefined,
  new URLSearchParams(window.location.search).get('view') === 'status' ? 'status' : 'workbench');
