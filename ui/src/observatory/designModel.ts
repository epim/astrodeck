export type Phase = 'planned' | 'running' | 'paused' | 'weather' | 'complete' | 'stopped';
export type Session = { phase: Phase; connected: boolean; accepted: number; total: number };
export type SessionAction = { type: 'start'; ready: boolean; total: number } | { type: 'pause' | 'resume' | 'cloud' | 'clear' | 'disconnect' | 'reconnect' | 'frame' | 'finish' | 'stop' };
export const INITIAL_SESSION: Session = { phase: 'planned', connected: true, accepted: 0, total: 40 };
export function sessionReducer(s: Session, a: SessionAction): Session {
  if (a.type === 'disconnect') return { ...s, connected: false };
  if (a.type === 'reconnect') return { ...s, connected: true };
  if (!s.connected) return s;
  if (a.type === 'start') return a.ready && !['running', 'paused', 'weather'].includes(s.phase) ? { ...s, phase: 'running', accepted: 0, total: Math.max(1, Math.round(a.total)) } : s;
  if (a.type === 'pause') return s.phase === 'running' ? { ...s, phase: 'paused' } : s;
  if (a.type === 'resume') return s.phase === 'paused' ? { ...s, phase: 'running' } : s;
  if (a.type === 'cloud') return s.phase === 'running' ? { ...s, phase: 'weather' } : s;
  if (a.type === 'clear') return s.phase === 'weather' ? { ...s, phase: 'running' } : s;
  if (a.type === 'frame') return s.phase === 'running' ? { ...s, accepted: Math.min(s.total, s.accepted + 1), phase: s.accepted + 1 >= s.total ? 'complete' : 'running' } : s;
  if (a.type === 'finish') return ['running', 'paused', 'weather'].includes(s.phase) ? { ...s, phase: 'complete', accepted: s.total } : s;
  if (a.type === 'stop') return ['running', 'paused', 'weather'].includes(s.phase) ? { ...s, phase: 'stopped' } : s;
  return s;
}
export const PHASE_LABEL: Record<Phase, string> = { planned: 'Ready when you are', running: 'Collecting light', paused: 'Paused by you', weather: 'Waiting for clear skies', complete: 'Your night, collected', stopped: 'Session stopped' };
export const PERSONAS = [
  { name: 'The first-night explorer', context: 'New camera, unfamiliar sky, one evening.', need: 'A good target and a clear next step.', path: 'Connect → choose → review → first image', mode: 'Guided', success: 'Can explain what the rig will do before starting.' },
  { name: 'The family observer', context: 'An adult and child sharing a telescope.', need: 'A growing picture, context, and short waits.', path: 'Explore → observe together → save a memory', mode: 'Guided + observing', success: 'Viewer can explore without moving the rig.' },
  { name: 'The field imager', context: 'Portable rig, gloves, limited power and network.', need: 'Fast alignment, repeatable settings, clear recovery.', path: 'Load rig → align → reuse plan → capture', mode: 'Workspace', success: 'Readiness and stop are usable on a phone.' },
  { name: 'The project imager', context: 'Multi-filter, multi-night target or mosaic.', need: 'A plan that tracks missing data and avoids waste.', path: 'Project → frame → schedule → inspect yield', mode: 'Workspace + AstroFlows', success: 'Every filter and panel has an explicit completion rule.' },
  { name: 'The remote operator', context: 'Unattended equipment and intermittent connections.', need: 'Trustworthy run state and controlled intervention.', path: 'Preflight → run → hold/recover → handover', mode: 'Workspace', success: 'A dropped browser connection never masquerades as a stopped rig.' },
  { name: 'The scientific observer', context: 'Time series, calibration, repeatable measurements.', need: 'Cadence, provenance, raw frames, and quality evidence.', path: 'Project → acquisition recipe → quality → export', mode: 'Workspace + measurement tools', success: 'Can trace a measurement back to the raw data and settings.' },
];
