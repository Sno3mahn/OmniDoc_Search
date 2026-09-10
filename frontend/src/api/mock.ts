import type {
  JobStream,
  OmniDocClient,
  QueryResponse,
  StartJobResponse,
  StreamEvent,
} from './types'

/** Status strings are copied verbatim from the StatusEmitterEvent calls in
 *  run_workflow.py, so the UI is built against the real vocabulary rather
 *  than invented placeholder text. */
const SCRIPT: Array<{ after: number; ev: StreamEvent }> = [
  { after: 400, ev: { type: 'status', data: 'Reading content from homepage' } },
  { after: 1800, ev: { type: 'status', data: 'Analyzed homepage content' } },
  { after: 900, ev: { type: 'status', data: 'Scanning contents for md links' } },
  { after: 2200, ev: { type: 'status', data: 'Fetched md links' } },
  { after: 700, ev: { type: 'status', data: 'Extracting content and saving files' } },
  { after: 500, ev: { type: 'status', data: 'Saving batch 0 of 3' } },
  { after: 300, ev: { type: 'status', data: 'Saving batch 1 of 3' } },
  { after: 300, ev: { type: 'status', data: 'Saving batch 2 of 3' } },
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

const MOCK_ANSWER =
  'Middleware in this framework runs before the route handler, and receives the request plus a `next` callback. Register it with `app.use()` for every route, or pass it per-route as the second argument. Ordering matters: middleware registered first runs first, and a middleware that never calls `next()` short-circuits the chain.'

export class MockClient implements OmniDocClient {
  async startJob(homepageUrl: string): Promise<StartJobResponse> {
    await delay(450)
    // Mirrors the SSRF gate in api.py so the UI exercises that failure path.
    if (/localhost|127\.0\.0\.1|192\.168\.|10\.|169\.254\./.test(homepageUrl)) {
      return { status: 'failed', message: 'homepage_url is not a permitted public address' }
    }
    return {
      status: 'success',
      job_id: 'mock-job-7f3c',
      collection_name: collectionNameFor(homepageUrl),
      message: 'triggered workflow',
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
          if (!cancelled) onEvent(step.ev)
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

  async query(_homepageUrl: string, question: string): Promise<QueryResponse> {
    await delay(1400)
    return {
      status: 'success',
      answer: MOCK_ANSWER,
      sources: [
        { text: `# Middleware\n\nMiddleware functions receive the request object...`, score: 0.82 },
        { text: `## Registering middleware\n\nUse app.use(fn) to apply globally...`, score: 0.74 },
        { text: `### Ordering\n\nMiddleware executes in registration order...`, score: 0.61 },
      ].filter(() => question.length > 0),
    }
  }
}

function collectionNameFor(url: string): string {
  const host = url.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0]
  return `doc-${host.replace(/[^a-z0-9]+/gi, '-')}`.toLowerCase()
}

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}
