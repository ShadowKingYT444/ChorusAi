import { useReducedMotion as useFramerReducedMotion } from 'framer-motion'

/**
 * Returns true if the user has requested reduced motion via system preferences.
 * Wraps Framer Motion's hook and provides a safe boolean (false during SSR).
 */
export function useReducedMotion(): boolean {
  const reduced = useFramerReducedMotion()
  return reduced ?? false
}
