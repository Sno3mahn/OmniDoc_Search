import { useState } from 'react'
import { getClient } from '../api/client'
import type { StartJobResponse } from '../api/types'
import { STAGE_ORDER } from '../lib/stages'
import './StartScreen.css'

interface Props {
  onStarted(job: { jobId: string; homepageUrl: string; apiKey: string; collection: string }): void
}

export function StartScreen({ onStarted }: Props) {
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const canRun = url.trim().length > 0 && !busy

  async function run() {
    if (!canRun) return
    setError('')
    setBusy(true)
    try {
      const res: StartJobResponse = await getClient().startJob(url.trim(), apiKey)
      if (res.status !== 'success' || !res.job_id) {
        setError(res.message)
        return
      }
      onStarted({
        jobId: res.job_id,
        homepageUrl: url.trim(),
        apiKey,
        collection: res.collection_name ?? '',
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="start">
      <div className="start__inner">
        <p className="mono-label start__eyebrow">Documentation ingestion pipeline</p>

        <h1 className="start__title">Point it at a documentation site.</h1>

        <p className="start__sub">
          Five agent-driven stages turn a docs site into a queryable index — homepage scan, markdown
          source detection, extraction, embedding, then answers with citations. Every stage streams
          live from <code>/ws/stream/&lt;job_id&gt;</code>.
        </p>

        {error && (
          <div className="start__error">
            <span>✕</span>
            <span>{error}</span>
          </div>
        )}

        <div className="start__row">
          <div className="prompt">
            <span className="prompt__sigil">▸</span>
            <input
              className="prompt__input"
              placeholder="https://docs.your-framework.com/"
              value={url}
              spellCheck={false}
              autoFocus
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run()}
            />
          </div>

          <div className="keyfield">
            <span className="keyfield__label">KEY</span>
            <input
              className="prompt__input"
              type="password"
              placeholder="x-api-key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run()}
            />
          </div>

          <button className="cmd" disabled={!canRun} onClick={run}>
            {busy ? 'Starting…' : <>Run pipeline<span className="cmd__kbd">⏎</span></>}
          </button>
        </div>

        <p className="start__hint">
          Private and loopback addresses are rejected by the API before any fetch happens.
        </p>

        <div className="stages">
          {STAGE_ORDER.map((s, i) => (
            <div key={s} style={{ display: 'flex', alignItems: 'center' }}>
              <div className="stages__item">
                <span className="stages__num">{String(i + 1).padStart(2, '0')}</span>
                <span>{s}</span>
              </div>
              {i < STAGE_ORDER.length - 1 && <span className="stages__sep">/</span>}
            </div>
          ))}
        </div>
      </div>
    </main>
  )
}
