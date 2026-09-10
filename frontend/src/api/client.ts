import { MockClient } from './mock'
import type { OmniDocClient } from './types'

export const API_MODE: 'mock' | 'live' =
  import.meta.env.VITE_API_MODE === 'live' ? 'live' : 'mock'

/** Single seam between the UI and the backend. The live adapter (fetch +
 *  WebSocket against api.py) lands when there's a running backend to test it
 *  against - shipping it untested would just be guesswork in a file that
 *  looks authoritative. */
export function getClient(): OmniDocClient {
  return new MockClient()
}
