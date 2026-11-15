import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const uploadAllowedMock = vi.fn()
const uploadFilesMock = vi.fn()
const uploadStatusMock = vi.fn()
const uploadResultMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual, // keep the real ApiError
    product: {
      ...actual.product,
      uploadAllowed: (...a: unknown[]) => uploadAllowedMock(...a),
      uploadFiles: (...a: unknown[]) => uploadFilesMock(...a),
      uploadStatus: (...a: unknown[]) => uploadStatusMock(...a),
      uploadResult: (...a: unknown[]) => uploadResultMock(...a),
    },
  }
})

// Imported after vi.mock so the component picks up the mocked product.
import { FloorUpload } from '../FloorUpload'
import { ApiError } from '../api/factory'

const ALLOWED = { extensions: ['.docx', '.pdf', '.xlsx'], accept: '.docx,.pdf,.xlsx', max_files: 10, max_file_bytes: 1000000, entitled: true }

function pick(name: string, type: string) {
  const input = screen.getByTestId('floor-upload-input') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['x'], name, { type })] } })
}

describe('FloorUpload — the Floor "+" document attach (F-10b)', () => {
  beforeEach(() => {
    uploadAllowedMock.mockReset().mockResolvedValue(ALLOWED)
    uploadFilesMock.mockReset().mockResolvedValue({ files_received: 1, job_id: 'j1' })
    uploadStatusMock.mockReset().mockResolvedValue({ status: 'parsed' })
    uploadResultMock.mockReset().mockResolvedValue({ status: 'completed', total_chunks: 3, failed_files: [] })
  })

  it('derives the accept-set from the backend (never hardwired) and enables the button', async () => {
    render(<FloorUpload sessionId="s1" />)
    await waitFor(() => expect(screen.getByTestId('floor-upload-add')).toBeEnabled())
    const input = screen.getByTestId('floor-upload-input') as HTMLInputElement
    expect(input.getAttribute('accept')).toBe('.docx,.pdf,.xlsx')
    expect(uploadAllowedMock).toHaveBeenCalledWith('s1')
  })

  it('uploads the picked files and shows the indexed result (filename + chunk count)', async () => {
    render(<FloorUpload sessionId="s1" />)
    await waitFor(() => expect(screen.getByTestId('floor-upload-add')).toBeEnabled())
    pick('spec.pdf', 'application/pdf')
    await waitFor(() => expect(uploadFilesMock).toHaveBeenCalledTimes(1))
    const [sid, files] = uploadFilesMock.mock.calls[0]
    expect(sid).toBe('s1')
    expect(files[0].name).toBe('spec.pdf')
    const result = await screen.findByTestId('floor-upload-result')
    expect(result).toHaveTextContent('spec.pdf')
    expect(result).toHaveTextContent('3 chunks indexed')
  })

  it('appends a second attachment instead of replacing the first (Task 3)', async () => {
    render(<FloorUpload sessionId="s1" />)
    await waitFor(() => expect(screen.getByTestId('floor-upload-add')).toBeEnabled())
    pick('spec.pdf', 'application/pdf')
    await screen.findByTestId('floor-upload-result')
    pick('boq.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    await waitFor(() => expect(uploadFilesMock).toHaveBeenCalledTimes(2))
    const chips = await screen.findAllByTestId('floor-upload-chip')
    expect(chips.map((c) => c.textContent)).toEqual(['spec.pdf', 'boq.xlsx'])
  })

  it('surfaces the backend 415 as an error and never shows a result', async () => {
    uploadFilesMock.mockRejectedValue(new ApiError(415, "File type '.exe' is not accepted"))
    render(<FloorUpload sessionId="s1" />)
    await waitFor(() => expect(screen.getByTestId('floor-upload-add')).toBeEnabled())
    pick('malware.exe', 'application/octet-stream')
    const err = await screen.findByTestId('floor-upload-error')
    expect(err).toHaveTextContent(/not accepted/)
    expect(screen.queryByTestId('floor-upload-result')).not.toBeInTheDocument()
  })

  it('stays disabled while the backend allowlist has not resolved', () => {
    uploadAllowedMock.mockReturnValue(new Promise(() => {})) // never resolves
    render(<FloorUpload sessionId="s1" />)
    expect(screen.getByTestId('floor-upload-add')).toBeDisabled()
  })

  it('hides the "+" and shows the upgrade message when not entitled (free tier)', async () => {
    uploadAllowedMock.mockResolvedValue({ ...ALLOWED, accept: '', entitled: false })
    render(<FloorUpload sessionId="s1" />)
    const add = await screen.findByTestId('floor-upload-add')
    await waitFor(() => expect(add).toBeDisabled())
    const upgrade = await screen.findByTestId('floor-upload-upgrade')
    expect(upgrade).toHaveTextContent(/paid-tier/i)
    expect(uploadFilesMock).not.toHaveBeenCalled()
  })
})
