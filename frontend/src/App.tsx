import { useState } from 'react'
import { API_MODE } from './api/client'
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

type View = 'start' | 'pipeline' | 'query'

export function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [view, setView] = useState<View>('start')

  return (
    <div className="app-shell">
      <header className="sysbar">
        <span className="sysbar__mark">
          omnidoc<span>://</span>search
        </span>
        <span className="sysbar__spacer" />
        <span className={`chip ${API_MODE === 'mock' ? 'chip--mock' : ''}`}>
          <span className="chip__dot" />
          {API_MODE} data
        </span>
      </header>

      {view === 'start' && (
        <StartScreen
          onStarted={(j) => {
            setSession({
              sources: [{ homepageUrl: j.homepageUrl, collection: j.collection }],
              apiKey: j.apiKey,
              jobId: j.jobId,
            })
            setView('pipeline')
          }}
          onOpenExisting={(t) => {
            setSession({ sources: t.sources, apiKey: t.apiKey })
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
            onReset={() => {
              setSession(null)
              setView('start')
            }}
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
              setSession(null)
              setView('start')
            }
          }}
        />
      )}
    </div>
  )
}
