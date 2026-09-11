// Shapes mirror what api.py actually returns - keep these in sync with the
// FastAPI handlers, not with what the UI wishes it got.

export interface StartJobResponse {
  /** 'cached' means the corpus registry already holds a current index for this
   *  URL, so no job was queued and there is no job_id to stream. */
  status: 'success' | 'failed' | 'cached'
  job_id?: string
  collection_name?: string
  /** Why a re-index was necessary, when one was. null on a fresh site. */
  reindex_reason?: string | null
  page_count?: number
  node_count?: number
  indexed_at?: number
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
  /** Page the chunk came from, and which doc set it belongs to. Both matter
   *  once a query spans more than one source. */
  source?: string
  collection?: string
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

export interface QueryStatus {
  ready: boolean
  collection_name: string
}

export interface OmniDocClient {
  startJob(homepageUrl: string, apiKey: string): Promise<StartJobResponse>
  subscribeJob(jobId: string, apiKey: string, onEvent: (ev: StreamEvent) => void): JobStream
  /** Takes several sources: a question spanning two projects' docs is the case
   *  no single vendor's built-in docs search can serve. */
  query(homepageUrls: string[], question: string, apiKey: string): Promise<QueryResponse>
  /** Whether this site already has a queryable index, so it can be opened
   *  without paying for a full re-extraction. */
  queryStatus(homepageUrl: string, apiKey: string): Promise<QueryStatus>
}
