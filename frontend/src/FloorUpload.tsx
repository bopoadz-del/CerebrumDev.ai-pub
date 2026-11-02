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
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const [names, setNames] = useState<string[]>([])
  const [result, setResult] = useState<UploadResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // The accept-set comes from the backend allowlist, not a frontend literal.
  useEffect(() => {
    let live = true
    product
      .uploadAllowed(sessionId)
      .then((a) => live && setAccept(a.accept))
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
      setNames(files.map((f) => f.name))
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
  const canAttach = !disabled && !busy && accept !== ''

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
