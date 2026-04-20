const WORKSPACE_ID_KEY = 'chorus_workspace_id'
const WORKSPACE_TOKEN_KEY = 'chorus_workspace_token'

export function readWorkspaceId(): string {
  const envValue = process.env.NEXT_PUBLIC_CHORUS_WORKSPACE_ID?.trim() ?? ''
  if (typeof window === 'undefined') return envValue
  return localStorage.getItem(WORKSPACE_ID_KEY)?.trim() ?? envValue
}

export function writeWorkspaceId(value: string): void {
  if (typeof window === 'undefined') return
  const trimmed = value.trim()
  if (trimmed) localStorage.setItem(WORKSPACE_ID_KEY, trimmed)
  else localStorage.removeItem(WORKSPACE_ID_KEY)
}

export function readWorkspaceToken(): string {
  const envValue = process.env.NEXT_PUBLIC_CHORUS_WORKSPACE_TOKEN?.trim() ?? ''
  if (typeof window === 'undefined') return envValue
  return sessionStorage.getItem(WORKSPACE_TOKEN_KEY)?.trim() ?? envValue
}

export function writeWorkspaceToken(value: string): void {
  if (typeof window === 'undefined') return
  const trimmed = value.trim()
  if (trimmed) sessionStorage.setItem(WORKSPACE_TOKEN_KEY, trimmed)
  else sessionStorage.removeItem(WORKSPACE_TOKEN_KEY)
}
