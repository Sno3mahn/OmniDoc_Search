import { useState } from 'react'
import { API_MODE } from './api/client'
import { StartScreen } from './screens/StartScreen'
import { PipelineScreen } from './screens/PipelineScreen'
import { QueryScreen } from './screens/QueryScreen'

/** jobId is optional: an already-indexed site can be opened straight into the
 *  query view without paying for a pipeline run. */
interface Session {
  homepageUrl: string
  apiKey: string
  collection: string
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
            setSession(j)
            setView('pipeline')
          }}
          onOpenExisting={(t) => {
            setSession(t)
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
            homepageUrl={session.homepageUrl}
            apiKey={session.apiKey}
            collection={session.collection}
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
          homepageUrl={session.homepageUrl}
          apiKey={session.apiKey}
          collection={session.collection}
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
