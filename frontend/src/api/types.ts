// Shapes mirror what api.py actually returns - keep these in sync with the
// FastAPI handlers, not with what the UI wishes it got.

export interface StartJobResponse {
  status: 'success' | 'failed'
  job_id?: string
  collection_name?: string
  message: string
}

/** Messages pushed over WS /ws/stream/{job_id} by _drive_workflow. */
export type StreamEvent =
  | { type: 'status'; data: string }
  | { type: 'pipeline_started'; data: { task_id: string } }
  | { type: 'done'; data: string }
  | { type: 'error'; data?: string; message?: string }

export interface QuerySource {
  text: string
  score: number | null
}

export interface QueryResponse {
  status: 'success' | 'failed'
  answer?: string
  sources?: QuerySource[]
  message?: string
}

export interface JobStream {
  close(): void
}

export interface OmniDocClient {
  startJob(homepageUrl: string, apiKey: string): Promise<StartJobResponse>
  subscribeJob(jobId: string, apiKey: string, onEvent: (ev: StreamEvent) => void): JobStream
  query(homepageUrl: string, question: string, apiKey: string): Promise<QueryResponse>
}
