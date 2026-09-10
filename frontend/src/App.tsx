import { useState } from 'react'
import { API_MODE } from './api/client'
import { StartScreen } from './screens/StartScreen'
import { PipelineScreen } from './screens/PipelineScreen'

interface ActiveJob {
  jobId: string
  homepageUrl: string
  apiKey: string
  collection: string
}

export function App() {
  const [job, setJob] = useState<ActiveJob | null>(null)

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

      {job ? (
        <PipelineScreen
          {...job}
          onReset={() => setJob(null)}
          onQuery={() => {
            /* screen 3 lands next increment */
          }}
        />
      ) : (
        <StartScreen onStarted={setJob} />
      )}
    </div>
  )
}
