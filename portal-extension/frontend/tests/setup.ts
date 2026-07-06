/**
 * Vitest 测试初始化 — 注册 jest-dom matchers + mock fetch 工具。
 */
import '@testing-library/jest-dom/vitest';

/**
 * 测试用的 fetch mock helper。
 *
 * 用法:
 *   mockFetch([
 *     { url: '/login', method: 'POST', status: 200, body: { username: 'admin', is_admin: true } },
 *     { url: '/me', status: 403, body: { detail: '未登录' } },
 *   ]);
 *
 * 未匹配的 fetch 调用返回 404。
 */
interface MockResponse {
  url: string;
  method?: string;
  status?: number;
  body?: unknown;
}

export function mockFetch(responses: MockResponse[]): typeof fetch {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];

  const fn = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input.toString();
    const method = (init?.method ?? 'GET').toUpperCase();
    let parsedBody: unknown = undefined;
    if (init?.body) {
      try {
        parsedBody = JSON.parse(String(init.body));
      } catch {
        parsedBody = String(init.body);
      }
    }
    calls.push({ url, method, body: parsedBody });

    const match = responses.find(
      (r) => r.url === url && (r.method ?? 'GET').toUpperCase() === method,
    );

    if (!match) {
      return new Response(JSON.stringify({ detail: 'not mocked' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const status = match.status ?? 200;
    const body = match.body ?? {};
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  // 暴露调用记录供测试断言
  (fn as unknown as { calls: typeof calls }).calls = calls;
  return fn as typeof fetch;
}

export function getFetchCalls(mock: typeof fetch): Array<{ url: string; method: string; body: unknown }> {
  return (mock as unknown as { calls: Array<{ url: string; method: string; body: unknown }> }).calls;
}
