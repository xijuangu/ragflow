import { Operator } from '@/constants/agent';
import { shouldUseCodeExecDebugLayout } from './utils';

describe('shouldUseCodeExecDebugLayout', () => {
  it('returns true only for CodeExec nodes', () => {
    expect(shouldUseCodeExecDebugLayout(Operator.Code)).toBe(true);
    expect(shouldUseCodeExecDebugLayout(Operator.Http)).toBe(false);
    expect(shouldUseCodeExecDebugLayout(undefined)).toBe(false);
  });
});
