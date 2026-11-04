import { expect, test } from 'bun:test';
import { publicDurableTurn } from './durable-turn-projection.mjs';

const errorBody = (code, status = code === 'busy' ? 429 : 409, extra = {}) => `${status}: ${JSON.stringify({
  message: 'private upstream detail token=synthetic-secret', type: 'local_codex_error', code, reason: code, ...extra,
})}`;

test('known local Codex failures expose a stable reason without leaking provider details or altering history', () => {
  for (const code of ['busy', 'tool_context_mismatch', 'tool_session_expired']) {
    for (const errorReason of ['model_error', 'provider_error']) {
      const internal = { turnId: 'turn', errorReason, error: errorBody(code), reply: '', cancelled: false,
        change: { createdNodeIds: ['saved-node'] }, proposals: [{ proposalId: 'already-proposed' }] };
      const before = JSON.stringify(internal);
      const projected = publicDurableTurn(internal);
      expect(projected.errorReason).toBe(`local_codex_${code}`);
      expect(JSON.stringify(projected)).not.toContain('synthetic-secret');
      expect(projected.change.createdNodeIds).toEqual(['saved-node']);
      expect(projected.proposals).toEqual([{ proposalId: 'already-proposed' }]);
      expect(JSON.stringify(internal)).toBe(before);
    }
  }
});

test('unknown, malformed, mismatched and unrelated provider errors remain opaque', () => {
  for (const error of [
    errorBody('other'), errorBody('busy', 403), errorBody('busy', 429, { type: 'another_provider' }),
    errorBody('busy', 429, { reason: 'another_reason' }), '429: {"code":"busy"}',
    '429: invalid JSON token=synthetic-secret', 'provider prose mentioning local_codex_error and busy',
  ]) {
    const projected = publicDurableTurn({ errorReason: 'model_error', error });
    expect(projected.errorReason).toBe('model_error');
    expect(projected.error).toContain('模型没有完成回复');
    expect(JSON.stringify(projected)).not.toContain('synthetic-secret');
  }
  expect(publicDurableTurn({ errorReason: 'turn_timeout', error: errorBody('busy') }).errorReason).toBe('turn_timeout');
});
