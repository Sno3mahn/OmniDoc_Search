import { LiveClient } from './live'
import { MockClient } from './mock'
import type { OmniDocClient } from './types'

export const API_MODE: 'mock' | 'live' =
  import.meta.env.VITE_API_MODE === 'live' ? 'live' : 'mock'

/** Single seam between the UI and the backend. Set VITE_API_MODE=live (see
 *  frontend/.env.local) to talk to a running api.py instead of the scripted
 *  mock. */
export function getClient(): OmniDocClient {
  return API_MODE === 'live' ? new LiveClient() : new MockClient()
}
