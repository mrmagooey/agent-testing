/**
 * Counter-based wrapper around page.route for scripting multi-step responses.
 * Register AFTER mockApi(page) so this handler wins (Playwright: last-registered first).
 *
 * const h = await sequencedMock(page, "**\/api\/status", [
 *   { status: 200, body: { state: "running" } },
 *   { status: 200, body: { state: "done" } },
 * ], { afterExhausted: "last" });
 * expect(h.callCount()).toBe(2);
 *
 * In E2E_LIVE=1 mode, this is a no-op; the returned handle's callCount() always returns 0.
 */
import type { Page, Route, Request } from '@playwright/test'

export interface SequencedResponse {
  status?: number
  body?: unknown
  contentType?: string
  delayMs?: number
}

export type SequencedResponseFn = (request: Request) => SequencedResponse | Promise<SequencedResponse>

export interface SequencedHandle {
  /** Number of times the route handler has been invoked. */
  callCount(): number
  /** Rewind the internal counter to zero so the sequence starts again. */
  reset(): void
  /** Remove the route override added by sequencedMock. */
  unroute(): Promise<void>
}

export interface SequencedMockOptions {
  /** HTTP method filter (case-insensitive). Omit to match any method. */
  method?: string
  /**
   * Behaviour once all responses have been consumed:
   * - `"last"` (default) — re-serve the final entry indefinitely.
   * - `"passthrough"` — call `route.fallback()` so the underlying mockApi handler responds.
   * - `"error"` — throw a descriptive Error (useful in tests that want to catch an over-call bug).
   */
  afterExhausted?: 'last' | 'passthrough' | 'error'
}

function fulfill(route: Route, entry: SequencedResponse): Promise<void> {
  const { status = 200, body, contentType = 'application/json', delayMs } = entry
  const serialized = body === undefined ? '' : JSON.stringify(body)
  if (delayMs && delayMs > 0) {
    return new Promise((resolve) =>
      setTimeout(
        () =>
          resolve(
            route.fulfill({
              status,
              contentType,
              body: serialized,
            }),
          ),
        delayMs,
      ),
    )
  }
  return route.fulfill({ status, contentType, body: serialized })
}

export async function sequencedMock(
  page: Page,
  urlPattern: string | RegExp,
  responses: Array<SequencedResponse | SequencedResponseFn>,
  options?: SequencedMockOptions,
): Promise<SequencedHandle> {
  if (process.env.E2E_LIVE === '1') {
    // No-op in live mode; return a no-op handle.
    return {
      callCount: () => 0,
      reset: () => undefined,
      unroute: async () => undefined,
    }
  }

  if (responses.length === 0) {
    throw new Error('sequencedMock: responses array must not be empty')
  }

  const afterExhausted = options?.afterExhausted ?? 'last'
  const methodFilter = options?.method?.toUpperCase()

  let counter = 0

  const handler = async (route: Route) => {
    if (methodFilter && route.request().method().toUpperCase() !== methodFilter) {
      return route.fallback()
    }

    const index = counter
    counter += 1

    if (index < responses.length) {
      const entry = responses[index]
      const resolved = typeof entry === 'function' ? await entry(route.request()) : entry
      return fulfill(route, resolved)
    }

    // Exhausted
    if (afterExhausted === 'last') {
      const last = responses[responses.length - 1]
      const resolved = typeof last === 'function' ? await last(route.request()) : last
      return fulfill(route, resolved)
    }

    if (afterExhausted === 'passthrough') {
      // fallback() passes to the next Playwright handler (e.g. mockApi's route)
      // rather than sending the request to the real network.
      return route.fallback()
    }

    // afterExhausted === 'error'
    throw new Error(
      `sequencedMock: all ${responses.length} response(s) were exhausted but the route was called again (call #${index + 1})`,
    )
  }

  await page.route(urlPattern, handler)

  return {
    callCount: () => counter,
    reset: () => {
      counter = 0
    },
    unroute: () => page.unroute(urlPattern, handler),
  }
}
