import { useCallback, useState } from 'react'
import { Navbar, type NavRun, type View } from './components/Navbar'
import { StartScreen } from './screens/StartScreen'
import { PipelineScreen } from './screens/PipelineScreen'
import { QueryScreen } from './screens/QueryScreen'

/** jobId is optional: an already-indexed site can be opened straight into the
 *  query view without paying for a pipeline run. */
interface Session {
  sources: Array<{ homepageUrl: string; collection: string }>
  apiKey: string
  jobId?: string
}

const IDLE: NavRun = { phase: 'idle' }

export function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [view, setView] = useState<View>('start')
  const [run, setRun] = useState<NavRun>(IDLE)

  const reset = useCallback(() => {
    setSession(null)
    setRun(IDLE)
    setView('start')
  }, [])

  // Stable so PipelineScreen's effect doesn't re-subscribe the WebSocket, and
  // only stores a change - the stream fires many events per stage.
  const handleRun = useCallback((next: NavRun) => {
    setRun((prev) => (prev.phase === next.phase && prev.stage === next.stage ? prev : next))
  }, [])

  return (
    <div className="app-shell">
      <Navbar
        view={view}
        run={run}
        sources={session?.sources ?? []}
        hasJob={Boolean(session?.jobId)}
        onNavigate={(v) => (v === 'start' ? reset() : setView(v))}
      />

      {view === 'start' && (
        <StartScreen
          onStarted={(j) => {
            setSession({
              sources: [{ homepageUrl: j.homepageUrl, collection: j.collection }],
              apiKey: j.apiKey,
              jobId: j.jobId,
            })
            setRun({ phase: 'running' })
            setView('pipeline')
          }}
          onOpenExisting={(t) => {
            setSession({ sources: t.sources, apiKey: t.apiKey })
            setRun(IDLE)
            setView('query')
          }}
        />
      )}

      {/* Kept mounted while querying - remounting would restart the stream and
          replay a run that already finished. display:contents keeps the
          wrapper out of the flex layout. */}
      {session?.jobId && (
        <div style={{ display: view === 'pipeline' ? 'contents' : 'none' }}>
          <PipelineScreen
            jobId={session.jobId}
            homepageUrl={session.sources[0].homepageUrl}
            apiKey={session.apiKey}
            collection={session.sources[0].collection}
            onRunChange={handleRun}
            onReset={reset}
            onQuery={() => setView('query')}
          />
        </div>
      )}

      {view === 'query' && session && (
        <QueryScreen
          sources={session.sources}
          apiKey={session.apiKey}
          hasRun={Boolean(session.jobId)}
          onBack={() => {
            if (session.jobId) {
              setView('pipeline')
            } else {
              reset()
            }
          }}
        />
      )}
    </div>
  )
}
