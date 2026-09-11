import type {
  JobStream,
  OmniDocClient,
  QueryResponse,
  QueryStatus,
  StartJobResponse,
  StreamEvent,
} from './types'

/** Module-level so it survives getClient() handing out fresh instances: once a
 *  scripted run finishes, that site reads as already-indexed, the same way the
 *  live backend would. */
const mockIndexed = new Set<string>()
let lastStartedUrl = ''

/** Status strings are copied verbatim from the StatusEmitterEvent calls in
 *  run_workflow.py, so the UI is built against the real vocabulary rather
 *  than invented placeholder text. */
const SCRIPT: Array<{ after: number; ev: StreamEvent }> = [
  { after: 400, ev: { type: 'status', data: 'Reading content from homepage' } },
  { after: 1800, ev: { type: 'status', data: 'Analyzed homepage content' } },
  { after: 900, ev: { type: 'status', data: 'Scanning contents for md links' } },
  { after: 2200, ev: { type: 'status', data: 'Fetched md links' } },
  { after: 700, ev: { type: 'status', data: 'Extracting content and saving files' } },
  { after: 500, ev: { type: 'status', data: 'Saving batch 1 of 3' } },
  { after: 300, ev: { type: 'status', data: 'Saving batch 2 of 3' } },
  { after: 300, ev: { type: 'status', data: 'Saving batch 3 of 3' } },
  { after: 1500, ev: { type: 'status', data: 'Completed saving batch of files' } },
  { after: 1100, ev: { type: 'status', data: 'Completed saving batch of files' } },
  {
    after: 900,
    ev: {
      type: 'status',
      data: 'Failed to save 1 file(s): https://docs.example.com/api/edge-cases (HTTP 404 fetching https://docs.example.com/api/edge-cases)',
    },
  },
  { after: 600, ev: { type: 'status', data: 'Completed saving batch of files' } },
  { after: 800, ev: { type: 'status', data: 'Saved all files' } },
  { after: 1200, ev: { type: 'pipeline_started', data: { task_id: 'c1f8a2e4-mock-task' } } },
  { after: 3000, ev: { type: 'done', data: 'partial success- 1 files missing' } },
]

interface CannedAnswer {
  match: RegExp
  answer: string
  sources: Array<{ text: string; score: number }>
}

/** A few keyed answers so a demo asking two questions doesn't obviously replay
 *  one canned string. Falls back to the middleware answer. */
const ANSWERS: CannedAnswer[] = [
  {
    match: /auth|token|login|session/i,
    answer:
      'Authentication is handled by a middleware that reads the `Authorization` header and attaches the resolved user to `request.state.user`. Tokens are verified against the signing key on every request — there is no server-side session store, so revocation is handled by keeping token lifetimes short rather than by invalidating a session record.',
    sources: [
      { text: '# Authentication\n\nEvery request carries a bearer token in the Authorization header...', score: 0.88 },
      { text: '## Token lifetime\n\nAccess tokens expire after 15 minutes. Refresh tokens...', score: 0.71 },
    ],
  },
  {
    match: /deploy|production|build|docker/i,
    answer:
      'For production, build the app with `npm run build` and serve the generated `dist/` directory from any static host. The server component expects `DATABASE_URL` and `SECRET_KEY` to be present in the environment at boot — it fails fast rather than starting with defaults.',
    sources: [
      { text: '# Deployment\n\nBuild output is fully static and can be served from a CDN...', score: 0.79 },
      { text: '## Required environment\n\nDATABASE_URL, SECRET_KEY, and optionally SENTRY_DSN...', score: 0.68 },
      { text: '### Health checks\n\nThe /healthz endpoint returns 200 once migrations have applied...', score: 0.55 },
    ],
  },
]

const DEFAULT_ANSWER: CannedAnswer = {
  match: /.*/,
  answer:
    'Middleware runs before the route handler and receives the request plus a `next` callback. Register it with `app.use()` to apply it to every route, or pass it per-route as the second argument. Ordering matters — middleware registered first runs first, and any middleware that never calls `next()` short-circuits the rest of the chain.',
  sources: [
    { text: '# Middleware\n\nMiddleware functions receive the request object and a next callback...', score: 0.82 },
    { text: '## Registering middleware\n\nUse app.use(fn) to apply a function globally...', score: 0.74 },
    { text: '### Ordering\n\nMiddleware executes in registration order. Short-circuiting...', score: 0.61 },
  ],
}

export class MockClient implements OmniDocClient {
  async startJob(homepageUrl: string): Promise<StartJobResponse> {
    await delay(450)
    // Mirrors the SSRF gate in api.py so the UI exercises that failure path.
    if (/localhost|127\.0\.0\.1|192\.168\.|10\.|169\.254\./.test(homepageUrl)) {
      return { status: 'failed', message: 'homepage_url is not a permitted public address' }
    }
    lastStartedUrl = homepageUrl
    return {
      status: 'success',
      job_id: 'mock-job-7f3c',
      collection_name: collectionNameFor(homepageUrl),
      message: 'triggered workflow',
    }
  }

  async queryStatus(homepageUrl: string): Promise<QueryStatus> {
    await delay(200)
    return {
      ready: mockIndexed.has(homepageUrl),
      collection_name: collectionNameFor(homepageUrl),
    }
  }

  subscribeJob(_jobId: string, _apiKey: string, onEvent: (ev: StreamEvent) => void): JobStream {
    let cancelled = false
    const timers: ReturnType<typeof setTimeout>[] = []

    let elapsed = 0
    for (const step of SCRIPT) {
      elapsed += step.after
      timers.push(
        setTimeout(() => {
          if (cancelled) return
          if (step.ev.type === 'done') mockIndexed.add(lastStartedUrl)
          onEvent(step.ev)
        }, elapsed),
      )
    }

    return {
      close() {
        cancelled = true
        timers.forEach(clearTimeout)
      },
    }
  }

  async query(homepageUrls: string[], question: string): Promise<QueryResponse> {
    await delay(1400)
    const hit = ANSWERS.find((a) => a.match.test(question)) ?? DEFAULT_ANSWER
    // Spread the mock chunks across the given sources so the multi-source
    // citation grouping has something realistic to render.
    const sources = hit.sources.map((s, i) => ({
      ...s,
      source: `tutorial / page-${i + 1}`,
      collection: collectionNameFor(homepageUrls[i % homepageUrls.length] ?? ''),
    }))
    return { status: 'success', answer: hit.answer, sources }
  }
}

function collectionNameFor(url: string): string {
  const host = url.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0]
  return `doc-${host.replace(/[^a-z0-9]+/gi, '-')}`.toLowerCase()
}

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}
