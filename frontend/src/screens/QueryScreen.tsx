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
  sources: Array<{ homepageUrl: string; collection: string }>
  apiKey: string
  /** False when opened directly against an existing index, so there's no run
   *  to go "back" to. */
  hasRun: boolean
  onBack(): void
}

let turnSeq = 0

function renderInlineCode(text: string) {
  return text.split(/(`[^`\n]+`)/g).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') && part.length > 2 ? (
      <code key={i}>{part.slice(1, -1)}</code>
    ) : (
      part
    ),
  )
}

/** Answers come back as markdown: prose with inline backticks AND fenced code
 *  blocks. Fences have to be pulled out first - treating the whole answer as
 *  inline spans turns a multi-line code sample into a wall of chips, which is
 *  exactly what happened the first time this met real DeepSeek output (the
 *  mock only ever produced inline backticks).
 *
 *  Rendered as React nodes, never innerHTML - this is model output. */
function renderAnswer(text: string) {
  const parts = text.split(/```[ \t]*(\w*)\n?([\s\S]*?)```/g)
  const out: React.ReactNode[] = []

  for (let i = 0; i < parts.length; i += 3) {
    const prose = parts[i]
    if (prose && prose.trim()) {
      out.push(
        <p key={`p${i}`} className="ans__p">
          {renderInlineCode(prose.trim())}
        </p>,
      )
    }
    const code = parts[i + 2]
    if (code !== undefined) {
      out.push(
        <pre key={`c${i}`} className="ans__pre">
          <code>{code.replace(/\n+$/, '')}</code>
        </pre>,
      )
    }
  }
  return out
}

export function QueryScreen({ sources, apiKey, hasRun, onBack }: Props) {
  const multi = sources.length > 1
  const label = multi ? `${sources.length} sources` : sources[0]?.collection ?? ''
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
      const res = await getClient().query(sources.map((s) => s.homepageUrl), question, apiKey)
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
          <p className="mono-label">{multi ? 'Querying across sources' : 'Index ready'}</p>
          <p className="qry__collection">{label}</p>
          <div className="qry__srcs">
            {sources.map((s) => (
              <span key={s.collection} className="qry__src">{s.collection}</span>
            ))}
          </div>
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
                <span>retrieving from {label}…</span>
              </div>
            )}

            {t.error && <div className="turn__error">✕ {t.error}</div>}

            {t.answer && <div className="turn__answer">{renderAnswer(t.answer)}</div>}

            {t.sources && t.sources.length > 0 && (
              <div className="srcs">
                <p className="mono-label srcs__label">
                  {t.sources.length} source chunk{t.sources.length === 1 ? '' : 's'}
                </p>
                {t.sources.map((s, i) => (
                  <div key={i} className="src">
                    <div className="src__meta">
                      <span className="src__idx">[{String(i + 1).padStart(2, '0')}]</span>
                      <span className="src__score">
                        <span className="src__bar">
                          <span style={{ width: `${Math.round((s.score ?? 0) * 100)}%` }} />
                        </span>
                        <span className="src__num">{(s.score ?? 0).toFixed(2)}</span>
                      </span>
                      {/* Which doc set, and which page inside it. Without this
                          a citation is unverifiable - you can read the chunk
                          but not go find it. */}
                      {multi && s.collection && (
                        <span className="src__coll">{s.collection}</span>
                      )}
                      {s.source && <span className="src__page">{s.source}</span>}
                    </div>
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
            {hasRun ? '← back to run' : '← new run'}
          </button>
        </div>
        <div ref={bottomRef} />
      </div>
    </main>
  )
}
