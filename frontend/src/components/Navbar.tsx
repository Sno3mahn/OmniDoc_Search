import { API_MODE } from '../api/client'
import './Navbar.css'

export type View = 'start' | 'pipeline' | 'query'

/** What the navbar needs to know about the run - deliberately not the whole
 *  RunState. The bar re-renders on every change it's given, and RunState
 *  changes on every streamed line; this only changes when the phase or the
 *  active stage does. */
export interface NavRun {
  phase: 'idle' | 'running' | 'done' | 'failed'
  stage?: string
}

interface Props {
  view: View
  run: NavRun
  sources: Array<{ collection: string }>
  /** A run exists to navigate back to. */
  hasJob: boolean
  onNavigate(view: View): void
}

const STEPS: Array<{ id: View; label: string }> = [
  { id: 'start', label: 'source' },
  { id: 'pipeline', label: 'pipeline' },
  { id: 'query', label: 'query' },
]

const ORDER: View[] = ['start', 'pipeline', 'query']

export function Navbar({ view, run, sources, hasJob, onNavigate }: Props) {
  const current = ORDER.indexOf(view)

  function reachable(step: View): boolean {
    if (step === view) return false
    switch (step) {
      // Going back to the source screen ends the session. Blocked mid-run so a
      // stray click can't discard a pipeline that's still streaming - the run
      // is the expensive thing here.
      case 'start':
        return run.phase !== 'running'
      case 'pipeline':
        return hasJob
      // Querying before the index exists returns nothing; a failed run has no
      // index at all.
      case 'query':
        return sources.length > 0 && (!hasJob || run.phase === 'done')
    }
  }

  function stateLabel(): string {
    switch (run.phase) {
      case 'running':
        return run.stage ? `running · ${run.stage}` : 'running'
      case 'done':
        return 'index ready'
      case 'failed':
        return 'run failed'
      default:
        return 'idle'
    }
  }

  return (
    <header className="nav">
      <span className="nav__mark">
        omnidoc<span>://</span>search
      </span>

      <nav className="steps" aria-label="Pipeline stages">
        {STEPS.map((s, i) => {
          const active = s.id === view
          const enabled = reachable(s.id)
          return (
            <button
              key={s.id}
              type="button"
              className={[
                'step',
                active ? 'step--active' : '',
                i < current ? 'step--visited' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              disabled={!enabled}
              aria-current={active ? 'step' : undefined}
              title={s.id === 'start' && run.phase === 'running' ? 'Run in progress' : undefined}
              onClick={() => onNavigate(s.id)}
            >
              <span className="step__n">{String(i + 1).padStart(2, '0')}</span>
              <span className="step__label">{s.label}</span>
            </button>
          )
        })}
      </nav>

      <span className="nav__spacer" />

      {sources.length > 0 && (
        <span className="nav__srcs" title={sources.map((s) => s.collection).join(', ')}>
          <b>{sources[0].collection}</b>
          {sources.length > 1 && <span className="nav__srcs-more">+{sources.length - 1}</span>}
        </span>
      )}

      <span className={`nav__state nav__state--${run.phase}`} aria-live="polite">
        <span className="nav__dot" />
        {stateLabel()}
      </span>

      <span className={`chip ${API_MODE === 'mock' ? 'chip--mock' : ''}`}>
        <span className="chip__dot" />
        {API_MODE} data
      </span>
    </header>
  )
}
