import { useEffect, useRef, useState } from 'react'
import { getClient } from '../api/client'
import type { QuerySource } from '../api/types'
import './QueryScreen.css'

interface Turn {
  id: number
  question: string
  answer?: string
  sources?: QuerySource[]
  error?: string
  pending: boolean
}

interface Props {
  homepageUrl: string
  apiKey: string
  collection: string
  onBack(): void
}

let turnSeq = 0

/** The LLM answers in prose that usually carries inline-code backticks. Render
 *  those as real code spans rather than leaking the markdown. Built as React
 *  nodes, not innerHTML - the answer text is model output, never trusted. */
function renderInlineCode(text: string) {
  return text.split(/(`[^`]+`)/g).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') && part.length > 2 ? (
      <code key={i}>{part.slice(1, -1)}</code>
    ) : (
      part
    ),
  )
}

export function QueryScreen({ homepageUrl, apiKey, collection, onBack }: Props) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  async function ask() {
    const question = draft.trim()
    if (!question) return
    const id = turnSeq++
    setTurns((t) => [...t, { id, question, pending: true }])
    setDraft('')

    try {
      const res = await getClient().query(homepageUrl, question, apiKey)
      setTurns((t) =>
        t.map((turn) =>
          turn.id === id
            ? turn.pending && res.status === 'success'
              ? { ...turn, pending: false, answer: res.answer, sources: res.sources }
              : { ...turn, pending: false, error: res.message ?? 'query failed' }
            : turn,
        ),
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'query failed'
      setTurns((t) => t.map((turn) => (turn.id === id ? { ...turn, pending: false, error: msg } : turn)))
    } finally {
      inputRef.current?.focus()
    }
  }

  return (
    <main className="qry">
      <div className="qry__inner">
        <header className="qry__head">
          <p className="mono-label">Index ready</p>
          <p className="qry__collection">{collection}</p>
          <p className="qry__meta">{homepageUrl}</p>
        </header>

        {turns.length === 0 && (
          <p className="qry__empty">
            Ask anything covered by the indexed pages. Answers cite the chunks they came from.
          </p>
        )}

        {turns.map((t) => (
          <article key={t.id} className="turn">
            <div className="turn__q">
              <span className="turn__sigil">▸</span>
              <span className="turn__qtext">{t.question}</span>
            </div>

            {t.pending && (
              <div className="turn__pending">
                <span className="dot" />
                <span>retrieving from {collection}…</span>
              </div>
            )}

            {t.error && <div className="turn__error">✕ {t.error}</div>}

            {t.answer && <p className="turn__answer">{renderInlineCode(t.answer)}</p>}

            {t.sources && t.sources.length > 0 && (
              <div className="srcs">
                <p className="mono-label srcs__label">
                  {t.sources.length} source chunk{t.sources.length === 1 ? '' : 's'}
                </p>
                {t.sources.map((s, i) => (
                  <div key={i} className="src">
                    <span className="src__idx">[{String(i + 1).padStart(2, '0')}]</span>
                    <span className="src__score">
                      <span className="src__bar">
                        <span style={{ width: `${Math.round((s.score ?? 0) * 100)}%` }} />
                      </span>
                      <span className="src__num">{(s.score ?? 0).toFixed(2)}</span>
                    </span>
                    {/* Chunks arrive as raw markdown slices; collapse blank
                        runs so a heading + body doesn't read as two orphans. */}
                    <span className="src__text">{s.text.replace(/\n{2,}/g, '\n')}</span>
                  </div>
                ))}
              </div>
            )}
          </article>
        ))}

        <div className="qry__prompt">
          <div className="prompt">
            <span className="prompt__sigil">▸</span>
            <input
              ref={inputRef}
              className="prompt__input"
              placeholder="ask the docs…"
              value={draft}
              spellCheck={false}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && ask()}
            />
          </div>
          <button className="cmd" disabled={!draft.trim()} onClick={ask}>
            Ask<span className="cmd__kbd">⏎</span>
          </button>
        </div>

        <div className="pipe__foot">
          <button className="linkish" onClick={onBack}>
            ← back to run
          </button>
        </div>
        <div ref={bottomRef} />
      </div>
    </main>
  )
}
