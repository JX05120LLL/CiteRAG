import { createApi } from './api/client';
import { mountVoicePage } from './features/voice/page';
import './styles.css';

let conversation = new URLSearchParams(window.location.search).get('conversation');
if (!conversation) {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem('citerag.workbench.selection') ?? 'null');
    if (value && typeof value === 'object' && 'chatId' in value && typeof value.chatId === 'string') conversation = value.chatId;
  } catch { /* An unavailable selection leaves the connect action disabled. */ }
}
const root = document.querySelector<HTMLElement>('#voice-app');
if (root) void mountVoicePage(root, createApi(), conversation);
