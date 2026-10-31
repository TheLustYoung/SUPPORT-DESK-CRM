import React from 'react';
import {createRoot} from 'react-dom/client';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import {App} from './App';
import './styles/main.scss';

// The saved theme before the first paint (no flash of the wrong theme). Light is the design's default.
try { document.documentElement.dataset.theme = localStorage.getItem('theme') === 'dark' ? 'dark' : 'light'; } catch {}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
