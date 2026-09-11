import { useState } from 'react'
import { API_MODE } from './api/client'
import { StartScreen } from './screens/StartScreen'
import { PipelineScreen } from './screens/PipelineScreen'
import { QueryScreen } from './screens/QueryScreen'

interface ActiveJob {
  jobId: string
  homepageUrl: string
  apiKey: string
  collection: string
}

type View = 'start' | 'pipeline' | 'query'

export function App() {
  const [job, setJob] = useState<ActiveJob | null>(null)
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
            setJob(j)
            setView('pipeline')
          }}
        />
      )}

      {/* Kept mounted while querying - remounting would restart the stream and
          replay a run that already finished. display:contents keeps the
          wrapper out of the flex layout. */}
      {job && (
        <div style={{ display: view === 'pipeline' ? 'contents' : 'none' }}>
          <PipelineScreen
            {...job}
            onReset={() => {
              setJob(null)
              setView('start')
            }}
            onQuery={() => setView('query')}
          />
        </div>
      )}

      {view === 'query' && job && (
        <QueryScreen
          homepageUrl={job.homepageUrl}
          apiKey={job.apiKey}
          collection={job.collection}
          onBack={() => setView('pipeline')}
        />
      )}
    </div>
  )
}
