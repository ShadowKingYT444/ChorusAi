const WORKSPACE_ID_KEY = 'chorus_workspace_id'
const WORKSPACE_TOKEN_KEY = 'chorus_workspace_token'

function randomWorkspaceSuffix(): string {
  if (typeof globalThis !== 'undefined' && globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().slice(0, 8)
  }
  return Math.random().toString(36).slice(2, 10)
}

function buildWorkspaceId(): string {
  return `workspace-${randomWorkspaceSuffix()}`
}

export function readWorkspaceId(): string {
  const envValue = process.env.NEXT_PUBLIC_CHORUS_WORKSPACE_ID?.trim() ?? ''
  if (typeof window === 'undefined') return envValue
  return localStorage.getItem(WORKSPACE_ID_KEY)?.trim() ?? envValue
}

export function getOrCreateWorkspaceId(): string {
  const existing = readWorkspaceId()
  if (existing) return existing
  if (typeof window === 'undefined') return ''
  const next = buildWorkspaceId()
  localStorage.setItem(WORKSPACE_ID_KEY, next)
  return next
}

export function regenerateWorkspaceId(): string {
  if (typeof window === 'undefined') return ''
  const next = buildWorkspaceId()
  localStorage.setItem(WORKSPACE_ID_KEY, next)
  return next
}

export function writeWorkspaceId(value: string): void {
  if (typeof window === 'undefined') return
  const trimmed = value.trim()
  if (trimmed) localStorage.setItem(WORKSPACE_ID_KEY, trimmed)
  else localStorage.removeItem(WORKSPACE_ID_KEY)
}

export function readWorkspaceToken(): string {
  if (typeof window === 'undefined') return ''
  return (
    localStorage.getItem(WORKSPACE_TOKEN_KEY)?.trim() ||
    sessionStorage.getItem(WORKSPACE_TOKEN_KEY)?.trim() ||
    ''
  )
}

export function writeWorkspaceToken(value: string): void {
  if (typeof window === 'undefined') return
  const trimmed = value.trim()
  if (trimmed) {
    localStorage.setItem(WORKSPACE_TOKEN_KEY, trimmed)
    sessionStorage.setItem(WORKSPACE_TOKEN_KEY, trimmed)
  } else {
    localStorage.removeItem(WORKSPACE_TOKEN_KEY)
    sessionStorage.removeItem(WORKSPACE_TOKEN_KEY)
  }
}
