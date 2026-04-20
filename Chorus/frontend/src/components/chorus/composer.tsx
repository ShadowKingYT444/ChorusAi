'use client'

import { useCallback, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { ArrowUp, Paperclip, Sparkles, X } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'
import type { NetworkStatus } from '@/hooks/use-network-status'
import type { AttachmentRecord } from '@/lib/api/orchestrator'
import { cn } from '@/lib/utils'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  disabled?: boolean
  status: NetworkStatus
  readyPeerCount?: number
  placeholder?: string
  voices: number
  rounds: number
  attachments: AttachmentRecord[]
  onAttachFiles: (files: File[]) => void
  onRemoveAttachment: (id: string) => void
  onClearAttachments: () => void
}

export function ChorusComposer({
  value,
  onChange,
  onSubmit,
  disabled,
  status,
  readyPeerCount,
  placeholder = 'Paste the plan, RFC, or brief you want reviewed…',
  voices,
  rounds,
  attachments,
  onAttachFiles,
  onRemoveAttachment,
  onClearAttachments,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const hasText = value.trim().length > 0
  const effectiveReady = readyPeerCount ?? status.online
  const canSend = (hasText || attachments.length > 0) && !disabled

  const autoSize = useCallback(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(220, el.scrollHeight)}px`
  }, [])

  useEffect(() => {
    autoSize()
  }, [value, autoSize])

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.metaKey) {
      e.preventDefault()
      if (canSend) onSubmit()
    }
  }

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileInput = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? [])
      if (files.length > 0) onAttachFiles(files)
      event.target.value = ''
    },
    [onAttachFiles],
  )

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      const files = Array.from(event.dataTransfer.files ?? [])
      if (files.length > 0) onAttachFiles(files)
    },
    [onAttachFiles],
  )

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 220, damping: 26 }}
      className="relative w-full"
    >
      <div
        className="relative overflow-hidden rounded-2xl"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
        style={{
          background: 'rgba(18,18,22,0.72)',
          border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 18px 60px -20px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.04)',
          backdropFilter: 'blur(14px)',
        }}
      >
        <div className="mx-4 mt-3 flex flex-wrap items-center gap-2 border-b border-white/6 pb-2">
          <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-white/72">
            {voices} voices
          </span>
          <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-white/72">
            {rounds} rounds
          </span>
          {attachments.length > 0 && (
            <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-white/72">
              {attachments.length} file{attachments.length === 1 ? '' : 's'}
            </span>
          )}
        </div>

        {attachments.length > 0 && (
          <div className="mx-4 mt-3 flex flex-wrap gap-2">
            {attachments.map((attachment) => (
              <div
                key={attachment.attachment_id}
                className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5"
              >
                <Paperclip className="h-3.5 w-3.5 text-white/55" />
                <div className="min-w-0">
                  <div className="max-w-[240px] truncate font-mono text-[10px] text-white/82">
                    {attachment.filename}
                  </div>
                  <div className="font-mono text-[9px] text-white/35">
                    {formatBytes(attachment.size_bytes)} · {attachment.kind}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(attachment.attachment_id)}
                  className="rounded-full p-1 text-white/35 transition-colors hover:bg-white/8 hover:text-white"
                  aria-label={`remove ${attachment.filename}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={onClearAttachments}
              className="rounded-full border border-white/10 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-white/55 transition-colors hover:bg-white/5 hover:text-white"
            >
              clear all
            </button>
          </div>
        )}

        <div className="flex gap-3 px-4 pb-2 pt-3">
          <Textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKey}
            placeholder={placeholder}
            rows={1}
            className={cn(
              'min-h-[72px] max-h-[220px] flex-1 resize-none border-none bg-transparent px-0 py-1',
              'font-sans text-[14.5px] leading-relaxed text-white/90 placeholder:text-white/35',
              'focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0',
            )}
          />
        </div>

        <div className="flex items-center gap-2 px-3 pb-3 pt-1">
          <button
            type="button"
            aria-label="attach"
            onClick={openFilePicker}
            className="rounded-md p-2 text-white/55 transition-colors hover:bg-white/5 hover:text-white"
          >
            <Paperclip className="h-4 w-4" />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={handleFileInput}
          />
          <button
            type="button"
            aria-label="enhance prompt"
            className="rounded-md p-2 text-white/55 transition-colors hover:bg-white/5 hover:text-white"
          >
            <Sparkles className="h-4 w-4" />
          </button>

          <div className="min-w-0 flex-1 px-1 font-sans text-[11.5px] text-white/42">
            Drop files here or attach notes, then ask for the strongest case for and against the plan, what evidence is missing, and the clearest next move.
          </div>

          <button
            type="button"
            onClick={() => canSend && onSubmit()}
            disabled={!canSend}
            aria-label="send"
            className="grid h-9 w-9 place-items-center rounded-lg transition-all"
            style={{
              background: canSend ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.06)',
              color: canSend ? '#0b0b0e' : 'rgba(255,255,255,0.35)',
              cursor: canSend ? 'pointer' : 'not-allowed',
              boxShadow: canSend ? '0 6px 22px -6px rgba(180,200,255,0.5)' : 'none',
            }}
          >
            <ArrowUp className="h-4 w-4" strokeWidth={2.4} />
          </button>
        </div>
      </div>

      <div className="mt-2 text-center font-mono text-[10px] tracking-[0.08em] text-white/35">
        {status.mode === 'live'
          ? effectiveReady > 0
            ? `Review capacity live · ${effectiveReady} reviewer${effectiveReady === 1 ? '' : 's'} visible plus any managed anchors`
            : 'Review capacity live · browser reviewers hidden, managed anchors may still route the job'
          : status.mode === 'unconfigured'
          ? 'Finish setup before opening the workspace'
          : 'Control plane unreachable · check the configured URL'}
      </div>
    </motion.div>
  )
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}
