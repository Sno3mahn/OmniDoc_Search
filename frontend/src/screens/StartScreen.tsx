import { useEffect, useState } from 'react'
import { getClient } from '../api/client'
import type { StartJobResponse } from '../api/types'
import { STAGE_ORDER } from '../lib/stages'
import './StartScreen.css'

export interface DocSource {
  homepageUrl: string
  collection: string
}

export interface OpenTarget {
  apiKey: string
  sources: DocSource[]
}

interface Props {
  onStarted(job: { jobId: string; homepageUrl: string; apiKey: string; collection: string }): void
  onOpenExisting(target: OpenTarget): void
}

export function StartScreen({ onStarted, onOpenExisting }: Props) {
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [existing, setExisting] = useState<string>('')
  const [sources, setSources] = useState<DocSource[]>([])

  const canRun = url.trim().length > 0 && !busy
  const alreadyAdded = sources.some((s) => s.homepageUrl === url.trim())

  function addSource() {
    const trimmed = url.trim()
    if (!existing || alreadyAdded) return
    setSources((prev) => [...prev, { homepageUrl: trimmed, collection: existing }])
    setUrl('')
  }

  // Re-extracting a site that's already indexed costs a full agent run, so
  // check whether one exists and offer to open it instead. Debounced, and
  // only once there's a key to authenticate the check with.
  useEffect(() => {
    const trimmed = url.trim()
    setExisting('')
    if (!trimmed || !apiKey) return

    let cancelled = false
    const t = setTimeout(async () => {
      try {
        const status = await getClient().queryStatus(trimmed, apiKey)
        if (!cancelled && status.ready) setExisting(status.collection_name)
      } catch {
        /* a failed probe just means no shortcut is offered */
      }
    }, 500)

    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [url, apiKey])

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

        {existing ? (
          <div className="start__existing">
            <span className="start__existing-text">
              <b>{existing}</b> is already indexed — add it instead of re-extracting.
            </span>
            <button className="cmd cmd--ghost" disabled={alreadyAdded} onClick={addSource}>
              {alreadyAdded ? 'Added' : '+ Add source'}
            </button>
          </div>
        ) : (
          <p className="start__hint">
            Private and loopback addresses are rejected by the API before any fetch happens.
          </p>
        )}

        {sources.length > 0 && (
          <div className="srcset">
            <p className="mono-label srcset__label">
              Querying across {sources.length} source{sources.length === 1 ? '' : 's'}
            </p>
            <div className="srcset__list">
              {sources.map((s) => (
                <span key={s.collection} className="srcchip">
                  {s.collection}
                  <button
                    className="srcchip__x"
                    aria-label={`Remove ${s.collection}`}
                    onClick={() =>
                      setSources((prev) => prev.filter((p) => p.collection !== s.collection))
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <button className="cmd cmd--ghost" onClick={() => onOpenExisting({ apiKey, sources })}>
              Query {sources.length > 1 ? 'these sources' : 'this source'} →
            </button>
          </div>
        )}

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
