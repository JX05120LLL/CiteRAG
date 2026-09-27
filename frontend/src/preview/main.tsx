import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { PreviewApp } from './App';
import '../react/theme.css';
const container = document.getElementById('preview-app');
if (!container) throw new Error('Preview entry container is missing');
createRoot(container).render(<StrictMode><PreviewApp /></StrictMode>);
