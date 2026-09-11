import type {
  JobStream,
  OmniDocClient,
  QueryResponse,
  StartJobResponse,
  StreamEvent,
} from './types'

const HTTP_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const WS_BASE = HTTP_BASE.replace(/^http/, 'ws')

export const WS_SUBPROTOCOL = 'omnidoc.v1'

/** Talks to api.py. Note the auth split: REST calls send X-API-Key as a normal
 *  header, but the browser WebSocket API can't set headers at all. The key
 *  rides in the Sec-WebSocket-Protocol header instead of a query param -
 *  query strings end up in server/proxy access logs, headers don't. */
export class LiveClient implements OmniDocClient {
  async startJob(homepageUrl: string, apiKey: string): Promise<StartJobResponse> {
    const res = await fetch(`${HTTP_BASE}/etl_workflow/`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': apiKey },
      body: JSON.stringify({ homepage_url: homepageUrl }),
    })
    if (!res.ok) {
      return { status: 'failed', message: await describeHttpError(res) }
    }
    return res.json()
  }

  subscribeJob(jobId: string, apiKey: string, onEvent: (ev: StreamEvent) => void): JobStream {
    const url = `${WS_BASE}/ws/stream/${encodeURIComponent(jobId)}`
    const ws = new WebSocket(url, [WS_SUBPROTOCOL, apiKey])
    let closedByUs = false

    ws.onmessage = (e) => {
      if (closedByUs) return
      try {
        onEvent(JSON.parse(e.data) as StreamEvent)
      } catch {
        onEvent({ type: 'error', data: `unparseable frame: ${String(e.data).slice(0, 200)}` })
      }
    }
    // Without the guard, tearing this socket down reports a failure the user
    // never had: React StrictMode mounts the effect twice in dev, and closing
    // the throwaway socket fires onerror, which latched the whole run as failed.
    ws.onerror = () => {
      if (!closedByUs) onEvent({ type: 'error', data: 'websocket connection failed' })
    }

    return {
      close() {
        closedByUs = true
        // Calling close() on a CONNECTING socket logs a console warning, so
        // wait for the handshake to land first. 1000 = normal closure.
        if (ws.readyState === WebSocket.CONNECTING) {
          ws.addEventListener('open', () => ws.close(1000), { once: true })
        } else if (ws.readyState === WebSocket.OPEN) {
          ws.close(1000)
        }
      },
    }
  }

  async query(homepageUrl: string, question: string, apiKey: string): Promise<QueryResponse> {
    const res = await fetch(`${HTTP_BASE}/query`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': apiKey },
      body: JSON.stringify({ query: question, homepage_url: homepageUrl }),
    })
    if (!res.ok) {
      return { status: 'failed', message: await describeHttpError(res) }
    }
    return res.json()
  }
}

/** 401/429 come back as FastAPI's {detail: ...} rather than the endpoints'
 *  own {status, message} shape, so they need unwrapping separately. */
async function describeHttpError(res: Response): Promise<string> {
  let detail = ''
  try {
    const body = await res.json()
    detail = body?.detail ?? body?.message ?? ''
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401) return detail || 'invalid API key'
  if (res.status === 429) return detail || 'rate limit exceeded'
  return detail || `request failed (${res.status})`
}
