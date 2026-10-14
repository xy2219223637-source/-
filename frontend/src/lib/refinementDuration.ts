import type {DurationPreference} from '../types/videoRefinement';

export function refinementDurationError(preference: DurationPreference, target: string, sourceMs: number): string {
  if (preference !== 'shorten' || !target.trim()) return '';
  const milliseconds = Math.round(Number(target) * 1000);
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '目标时长必须大于 0 秒';
  if (milliseconds >= sourceMs) return `缩短目标必须小于原片时长 ${(sourceMs / 1000).toFixed(2)} 秒`;
  return '';
}
