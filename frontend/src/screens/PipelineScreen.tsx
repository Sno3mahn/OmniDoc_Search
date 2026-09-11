import { useEffect, useRef, useState } from 'react'
import { getClient } from '../api/client'
import type { NavRun } from '../components/Navbar'
import {
  applyEvent,
  emptyRun,
  fmtClock,
  fmtElapsed,
  STAGE_ORDER,
  type RunState,
  type StageState,
} from '../lib/stages'
import './PipelineScreen.css'

interface Props {
  jobId: string
  homepageUrl: string
  apiKey: string
  collection: string
  /** Reports the run's phase up to the navbar, which stays visible after you
   *  navigate away from this screen. */
  onRunChange(run: NavRun): void
  onReset(): void
  onQuery(): void
}

export function PipelineScreen({
  jobId,
  homepageUrl,
  apiKey,
  collection,
  onRunChange,
  onReset,
  onQuery,
}: Props) {
  const [run, setRun] = useState<RunState>(emptyRun)
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(Date.now())
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const started = startRef.current
    const stream = getClient().subscribeJob(jobId, apiKey, (ev) => {
      setRun((prev) => applyEvent(prev, ev, Date.now() - started))
    })
    return () => stream.close()
  }, [jobId, apiKey])

  useEffect(() => {
    onRunChange(navRun(run))
  }, [run, onRunChange])

  useEffect(() => {
    if (run.finished) return
    const t = setInterval(() => setElapsed(Date.now() - startRef.current), 100)
    return () => clearInterval(t)
  }, [run.finished])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [run])

  const shown = run.finished ? lastTimestamp(run) : elapsed

  return (
    <main className="pipe">
      <div className="pipe__inner">
        <header className="runhead">
          <div className="runhead__main">
            <p className="mono-label runhead__id">Run · {jobId}</p>
            <p className="runhead__url">{homepageUrl}</p>
            <div className="runhead__meta">
              <span>
                collection <b>{collection}</b>
              </span>
              {run.taskId && (
                <span>
                  ingest task <b>{run.taskId}</b>
                </span>
              )}
            </div>
          </div>
          <div className={`clock ${run.finished ? '' : 'clock--running'}`}>{fmtClock(shown)}</div>
        </header>

        <div className="tl__head">
          <span className="tl__head-label">stage</span>
          <span className="stage__spacer" />
          <span className="stage__stat">status</span>
          <span className="stage__dur">duration</span>
        </div>

        <div>
          {STAGE_ORDER.map((id) => (
            <StageBlock key={id} stage={run.stages[id]} />
          ))}
        </div>

        {run.finalStatus && <Result status={run.finalStatus} collection={collection} onQuery={onQuery} />}
        {run.failure && !run.finalStatus && (
          <div className="result result--fail">
            <div className="result__main">
              <div className="result__status">Run failed</div>
              <div className="result__sub">{run.failure}</div>
            </div>
          </div>
        )}

        <div className="pipe__foot">
          <button className="linkish" onClick={onReset}>
            ← new run
          </button>
        </div>
        <div ref={bottomRef} />
      </div>
    </main>
  )
}

function StageBlock({ stage }: { stage: StageState }) {
  // 'ready' is an instant, not a span - a 0.0s duration there is noise.
  const span =
    stage.startedAt !== undefined && stage.endedAt !== undefined
      ? stage.endedAt - stage.startedAt
      : 0
  const dur = span >= 50 ? fmtElapsed(span) : ''

  return (
    <section className={`stage stage--${stage.status}`}>
      <span className="stage__marker" />
      <div className="stage__head">
        <span className="stage__name">{stage.id}</span>
        {stage.branch && <span className="stage__branch">{stage.branch}</span>}
        <span className="stage__spacer" />
        <span className="stage__stat">{stage.status}</span>
        <span className="stage__dur">{dur}</span>
      </div>
      <div className="stage__body">
        {stage.entries.map((e) => (
          <div key={e.id} className={`line ${e.error ? 'line--error' : ''}`}>
            <span className="line__glyph">{e.error ? '✕' : '│'}</span>
            <span className="line__text">{e.text}</span>
            <span className="line__at">+{fmtElapsed(e.at)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function Result({
  status,
  collection,
  onQuery,
}: {
  status: string
  collection: string
  onQuery(): void
}) {
  const kind = status.includes('failed') ? 'fail' : status.includes('partial') ? 'partial' : 'ok'
  return (
    <div className={`result result--${kind}`}>
      <div className="result__main">
        <div className="result__status">{status}</div>
        <div className="result__sub">indexed into {collection}</div>
      </div>
      {kind !== 'fail' && (
        <button className="cmd" onClick={onQuery}>
          Query this index →
        </button>
      )}
    </div>
  )
}

/** Collapses RunState down to what the navbar shows. 'partial success' counts
 *  as done: an index exists and is queryable, which is the only thing the
 *  navbar gates on. */
function navRun(run: RunState): NavRun {
  if (run.failure || run.stages.ready.status === 'failed') return { phase: 'failed' }
  if (run.finished) return { phase: 'done' }
  const active = [...STAGE_ORDER].reverse().find((id) => run.stages[id].status === 'running')
  return active ? { phase: 'running', stage: active } : { phase: 'running' }
}

function lastTimestamp(run: RunState): number {
  let max = 0
  for (const id of STAGE_ORDER) {
    for (const e of run.stages[id].entries) max = Math.max(max, e.at)
    const end = run.stages[id].endedAt
    if (end !== undefined) max = Math.max(max, end)
  }
  return max
}
