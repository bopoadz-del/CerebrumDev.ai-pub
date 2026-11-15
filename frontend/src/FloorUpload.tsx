/**
 * F-10b: the Floor composer "+" — attach PDF / DOCX / XLSX documents that seed
 * the build. The accept-set is DERIVED from the backend (GET /upload/allowed),
 * never hardwired here, so the picker can never drift from the POST route's
 * 415. A disallowed type the OS picker still lets through is refused by the
 * backend and surfaced as the error message. Upload parses in the background,
 * so we poll /upload/status to a terminal state and then read /upload/result.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { product, ApiError, type UploadResult } from './api/factory'

const TERMINAL = ['parsed', 'completed', 'completed_with_warnings', 'failed']

interface Props {
  sessionId: string
  disabled?: boolean
}

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

export function FloorUpload({ sessionId, disabled }: Props) {
  const [accept, setAccept] = useState('')
  const [entitled, setEntitled] = useState<boolean | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const [names, setNames] = useState<string[]>([])
  const [result, setResult] = useState<UploadResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // The accept-set comes from the backend allowlist, not a frontend literal.
  // Upload is a paid action: `entitled` gates the "+" and, when false, shows
  // the upgrade message instead of a picker the backend would refuse anyway.
  useEffect(() => {
    let live = true
    product
      .uploadAllowed(sessionId)
      .then((a) => {
        if (!live) return
        setAccept(a.accept)
        setEntitled(a.entitled)
      })
      .catch(() => {
        /* leave accept empty — the "+" stays disabled until it resolves */
      })
    return () => {
      live = false
    }
  }, [sessionId])

  const onPick = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      setPhase('uploading')
      setMessage('')
      setResult(null)
      // Task 3: attach is APPEND, not replace. Keep every file the user has
      // attached this session; a second pick adds beside the first instead of
      // silently dropping it. Dedup by name so re-selecting a file keeps one chip.
      setNames((prev) => {
        const next = [...prev]
        for (const f of files) {
          if (!next.includes(f.name)) next.push(f.name)
        }
        return next
      })
      try {
        await product.uploadFiles(sessionId, files)
        setPhase('processing')
        for (let i = 0; i < 180; i++) {
          const s = await product.uploadStatus(sessionId)
          if (TERMINAL.includes(s.status)) {
            const r = await product.uploadResult(sessionId)
            setResult(r)
            setMessage(r.message || '')
            setPhase(r.status === 'failed' ? 'error' : 'done')
            return
          }
          await new Promise((res) => setTimeout(res, 1000))
        }
        setPhase('error')
        setMessage('Upload is taking longer than expected; check back shortly.')
      } catch (e) {
        setPhase('error')
        setMessage(e instanceof ApiError ? e.message : 'Upload failed.')
      }
    },
    [sessionId],
  )

  const busy = phase === 'uploading' || phase === 'processing'
  const canAttach = !disabled && !busy && accept !== '' && entitled === true
  const showUpgrade = entitled === false

  // The transient message is a SIBLING of the "+" control, not its child:
  // inside the composer's flex row the message takes a full-width line of its
  // own (`.floor-upload__message`), so a long error can never shrink into a
  // sliver over the button or push the row — and the page — sideways on a
  // phone.
  return (
    <>
      <span className="floor-upload" data-testid="floor-upload">
        <button
          type="button"
          className="floor-upload__add"
          data-testid="floor-upload-add"
          aria-label="Attach documents (PDF, DOCX, XLSX)"
          title={
            accept
              ? `Attach documents — ${accept.replace(/\./g, '').replace(/,/g, ', ').toUpperCase()} only`
              : 'Attach documents'
          }
          disabled={!canAttach}
          onClick={() => inputRef.current?.click()}
        >
          +
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={accept}
          hidden
          data-testid="floor-upload-input"
          onChange={(e) => {
            const files = Array.from(e.target.files || [])
            e.target.value = '' // allow re-selecting the same file
            void onPick(files)
          }}
        />
      </span>
      {showUpgrade && (
        <span
          className="floor-upload__message floor-upload__message--upgrade"
          data-testid="floor-upload-upgrade"
          aria-live="polite"
        >
          Document attachments are a paid-tier feature — upgrade to attach files.
        </span>
      )}
      {names.length > 0 && (
        <span
          className="floor-upload__chips"
          data-testid="floor-upload-chips"
          aria-label="Attached documents"
        >
          {names.map((n) => (
            <span key={n} className="floor-upload__chip" data-testid="floor-upload-chip">
              {n}
            </span>
          ))}
        </span>
      )}
      {busy && (
        <span
          className="floor-upload__status floor-upload__message"
          data-testid="floor-upload-status"
          aria-live="polite"
        >
          {phase === 'uploading' ? 'Uploading…' : 'Reading documents…'}
        </span>
      )}
      {phase === 'error' && (
        <span
          className="error-box floor-upload__message"
          data-testid="floor-upload-error"
          aria-live="polite"
        >
          {message || 'Upload failed.'}
        </span>
      )}
      {phase === 'done' && (
        <span
          className="floor-upload__result floor-upload__message"
          data-testid="floor-upload-result"
          aria-live="polite"
        >
          {names.join(', ')} — {result?.total_chunks ?? 0} chunk
          {(result?.total_chunks ?? 0) === 1 ? '' : 's'} indexed
          {result?.failed_files && result.failed_files.length > 0
            ? ` (${result.failed_files.length} failed)`
            : ''}
        </span>
      )}
    </>
  )
}
