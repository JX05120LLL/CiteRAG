import { memo, useEffect, useLayoutEffect } from 'react';
import { addAfterEffect, Canvas, useThree } from '@react-three/fiber';
import type { EchoState, EchoTone } from './voiceVisualModel';
import { palette } from './theme';

const colors: Record<EchoTone, string> = {
  quiet: '#99b4a7', listening: palette.mint, working: '#73b49a', sending: palette.mint, attention: '#e2b573',
};

function ContextGuard({ onUnavailable, onFirstFrame }: { onUnavailable: () => void; onFirstFrame: () => void }) {
  const renderer = useThree((state) => state.gl);
  const canvas = renderer.domElement;
  useEffect(() => {
    canvas.addEventListener('webglcontextlost', onUnavailable);
    return () => canvas.removeEventListener('webglcontextlost', onUnavailable);
  }, [canvas, onUnavailable]);
  useLayoutEffect(() => {
    let alive = true;
    // onCreated precedes drawing. Observe this renderer after the shared render loop.
    const remove = addAfterEffect(() => {
      if (!alive || renderer.info.render.calls === 0) return;
      alive = false;
      remove();
      onFirstFrame();
    });
    return () => { alive = false; remove(); };
  }, [renderer, onFirstFrame]);
  return null;
}

function EchoRings({ echo }: { echo: EchoState }) {
  const peak = Math.max(...echo.levels);
  return <group rotation={[-0.48, 0.24, -0.2]}>
    {[0, 1, 2].map((index) => <mesh key={index} position={[0, 0, (index - 1) * 0.17]}
      rotation={[index * 0.4 + echo.levels[index] * 0.08, index * -0.25, 0]}
      scale={1 + echo.levels[index] * 0.12}>
      <torusGeometry args={[0.94 - index * 0.12, index === 1 ? 0.042 : 0.017, 10, 112]} />
      <meshStandardMaterial color={colors[echo.tone]} roughness={0.28} metalness={0.65}
        transparent opacity={index === 1 ? 0.94 : 0.56} />
    </mesh>)}
    <mesh scale={1 + peak * 0.1}>
      <sphereGeometry args={[0.14, 24, 16]} />
      <meshStandardMaterial color={colors[echo.tone]} roughness={0.22} metalness={0.42} />
    </mesh>
    {echo.levels.map((level, index) => <mesh key={index} position={[(index - 2) * 0.075, 0, 0.26]} scale={[1, 1 + level * 3, 1]}>
      <capsuleGeometry args={[0.013, 0.04, 4, 8]} />
      <meshBasicMaterial color={colors[echo.tone]} />
    </mesh>)}
  </group>;
}

interface EchoProps { echo: EchoState; onUnavailable: () => void; onFirstFrame: () => void }

function VoiceThreeEcho({ echo, onUnavailable, onFirstFrame }: EchoProps) {
  return <Canvas className="voice-echo-canvas" frameloop="demand" dpr={[1, 1.5]}
    camera={{ position: [0, 0, 3.6], fov: 42 }}
    gl={{ alpha: true, antialias: true, powerPreference: 'low-power' }}
    resize={{ debounce: { scroll: 100, resize: 100 } }}>
    <ContextGuard onUnavailable={onUnavailable} onFirstFrame={onFirstFrame} />
    <ambientLight intensity={2.5} />
    <directionalLight position={[-3, 4, 4]} intensity={3} />
    <directionalLight position={[3, -2, -1]} intensity={1.4} />
    <EchoRings echo={echo} />
  </Canvas>;
}

// The analyser sends each animation frame, including repeated silent bands.
// Only render changes the scene can show; input/output labels remain outside Canvas.
export default memo(VoiceThreeEcho, (previous, next) => previous.onUnavailable === next.onUnavailable
  && previous.onFirstFrame === next.onFirstFrame
  && previous.echo.tone === next.echo.tone
  && previous.echo.levels.length === next.echo.levels.length
  && previous.echo.levels.every((level, index) => level === next.echo.levels[index]));
