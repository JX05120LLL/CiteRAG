import { Component, lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { VoiceState } from '../features/voice/controller';
import { voiceVisualState } from './voiceVisualModel';
import type { EchoState } from './voiceVisualModel';
import './voice-visual.css';

const ThreeEcho = lazy(() => import('./VoiceThreeEcho'));

function StaticEcho({ echo }: { echo: EchoState }) {
  return <svg className="voice-echo-static" viewBox="0 0 360 260" aria-hidden="true">
    <g fill="none" stroke="currentColor">
      <ellipse cx="180" cy="130" rx="100" ry="58" strokeWidth="2" transform="rotate(-27 180 130)" />
      <ellipse cx="180" cy="130" rx="85" ry="69" strokeWidth="5" transform="rotate(22 180 130)" />
      <ellipse cx="180" cy="130" rx="70" ry="86" strokeWidth="2" transform="rotate(-12 180 130)" />
    </g>
    <g className="voice-echo-input" fill="currentColor">
      {echo.levels.map((level, index) => <rect key={index} x={156 + index * 10} y={125 - level * 16}
        width="6" height={10 + level * 32} rx="3" />)}
    </g>
  </svg>;
}

function ThreeStage({ echo, onUnavailable }: { echo: EchoState; onUnavailable: () => void }) {
  const [drawn, setDrawn] = useState(false);
  const mounted = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const onFirstFrame = useCallback(() => { if (mounted.current) setDrawn(true); }, []);
  return <div className="voice-echo-3d" data-drawn={drawn ? 'true' : 'false'}
    style={{ position: 'relative', width: '100%', height: '100%' }}>
    {!drawn && <div className="voice-echo-placeholder" style={{ position: 'absolute', inset: 0, zIndex: 1, background: 'var(--voice-stage, #142621)' }}>
      <StaticEcho echo={echo} />
    </div>}
    <ThreeEcho echo={echo} onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />
  </div>;
}

class VisualBoundary extends Component<{ fallback: ReactNode; children: ReactNode; onFailure: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onFailure(); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function useVisualEnvironment() {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? true);
  useEffect(() => {
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const visibility = () => setVisible(document.visibilityState !== 'hidden');
    const preference = () => setReduced(motion?.matches ?? true);
    document.addEventListener('visibilitychange', visibility);
    motion?.addEventListener('change', preference);
    return () => {
      document.removeEventListener('visibilitychange', visibility);
      motion?.removeEventListener('change', preference);
    };
  }, []);
  return { visible, reduced };
}

export function VoiceVisual({ media, levels }: { media: VoiceState; levels: readonly number[] }) {
  const echo = voiceVisualState(media, levels);
  const { visible, reduced } = useVisualEnvironment();
  const [unavailable, setUnavailable] = useState(false);
  const onUnavailable = useCallback(() => setUnavailable(true), []);
  const supportsWebGL = typeof window.WebGL2RenderingContext !== 'undefined';
  const enhanced = supportsWebGL && !reduced && visible && !unavailable;
  const fallback = <StaticEcho echo={echo} />;
  return <div className={`voice-echo voice-echo-${echo.tone}`} data-input={echo.input}
    data-output={echo.output} data-renderer={enhanced ? 'three' : 'static'} aria-hidden="true">
    <VisualBoundary fallback={fallback} onFailure={onUnavailable}><Suspense fallback={fallback}>
      {enhanced ? <ThreeStage echo={echo} onUnavailable={onUnavailable} /> : fallback}
    </Suspense></VisualBoundary>
  </div>;
}
