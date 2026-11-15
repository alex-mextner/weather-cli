/**
 * @file  Fetch with a hard deadline and tiny structural guards for untrusted JSON.
 *
 * Accessed via: every provider. A voice answer must not wait on a slow network, so every
 * request carries `AbortSignal.timeout`; a late answer is an error, never a delay.
 *
 * Assumptions: provider payloads are never asserted into a type. They are narrowed field by
 * field with the guards below, and a missing optional field becomes `undefined`.
 */

export type JsonRecord = { [key: string]: JsonValue };
export type JsonValue = string | number | boolean | null | JsonValue[] | JsonRecord;

export function isRecord(value: JsonValue | undefined): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function asArray(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? value : [];
}

/** A finite number, or undefined for anything else (null, string, NaN). */
export function asNumber(value: JsonValue | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export function asString(value: JsonValue | undefined): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

/** The slice of `fetch` this library uses; tests and hosts can supply their own. */
export interface HttpInit {
  method: 'GET' | 'POST';
  headers: Record<string, string>;
  body?: string;
  signal: AbortSignal;
}

export interface HttpResponse {
  ok: boolean;
  status: number;
  text(): Promise<string>;
}

export type Fetcher = (url: string, init: HttpInit) => Promise<HttpResponse>;

export interface RequestOptions {
  timeoutMs: number;
  headers?: Record<string, string>;
  /** JSON body; switches the method to POST. */
  body?: JsonValue;
  fetchImpl?: Fetcher;
}

export class ProviderError extends Error {
  constructor(
    readonly provider: string,
    message: string,
  ) {
    super(`${provider}: ${message}`);
  }
}

export async function requestJson(
  provider: string,
  url: string,
  options: RequestOptions,
): Promise<JsonValue> {
  const doFetch: Fetcher = options.fetchImpl ?? ((target, init) => fetch(target, init));
  let response: HttpResponse;
  try {
    response = await doFetch(url, {
      method: options.body === undefined ? 'GET' : 'POST',
      headers: {
        accept: 'application/json',
        ...(options.body === undefined ? {} : { 'content-type': 'application/json' }),
        ...options.headers,
      },
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
      signal: AbortSignal.timeout(options.timeoutMs),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new ProviderError(provider, `request failed (${reason})`);
  }
  if (!response.ok) throw new ProviderError(provider, `HTTP ${response.status}`);
  try {
    // SAFETY: JSON.parse yields exactly the JsonValue union; guards narrow it before use.
    const parsed: JsonValue = JSON.parse(await response.text());
    return parsed;
  } catch {
    throw new ProviderError(provider, 'response is not JSON');
  }
}
