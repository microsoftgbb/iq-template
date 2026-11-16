// Checks the bearer token on each question before the server passes it to Foundry: a Microsoft Entra access token
// for Foundry (https://ai.azure.com), signed by Entra, unexpired, issued in this tenant to this app's sign-in
// registration with the user_impersonation scope. Foundry still authorizes the user; this check only keeps the
// server from relaying anyone else's tokens. Tokens are never logged or kept.
import { createRemoteJWKSet, errors, jwtVerify, type JWTVerifyGetKey } from 'jose'

// The resource behind https://ai.azure.com tokens (Azure Machine Learning Services): v1 tokens name its URI, v2 its ID.
export const FOUNDRY_AUDIENCES = ['https://ai.azure.com', 'https://ai.azure.com/', '18a66f5f-dbdf-4c17-9dd7-1634712a9cbe']
const BEARER = /^Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$/

export type Verified = { ok: true; token: string } | { ok: false; message: string }
export type Verifier = ((authorization: string | undefined) => Promise<Verified>) & { warmUp: () => Promise<void> }

export interface VerifierOptions {
  tenantId: string
  clientId: string
  // Tests supply local keys; the server reads the tenant's published signing keys.
  keys?: JWTVerifyGetKey
  currentDate?: Date
}

function rejected(error: unknown): string {
  if (error instanceof errors.JWTExpired) return 'Your sign-in expired. Reload the page to sign in again.'
  if (error instanceof errors.JWTClaimValidationFailed) return 'This sign-in is not for this app. Reload the page to sign in again.'
  if (error instanceof errors.JWKSTimeout || error instanceof errors.JOSEError && error.code === 'ERR_JWKS_NO_MATCHING_KEY') {
    return "The app server could not check your sign-in against Microsoft Entra's keys. Ask again."
  }
  return 'The app server did not accept your sign-in. Reload the page to sign in again.'
}

export function createTokenVerifier(options: VerifierOptions): Verifier {
  const tenantId = options.tenantId.toLowerCase()
  const clientId = options.clientId.toLowerCase()
  const remote = options.keys ? undefined : createRemoteJWKSet(
    new URL(`https://login.microsoftonline.com/${tenantId}/discovery/v2.0/keys`),
    { cooldownDuration: 30_000, cacheMaxAge: 6 * 60 * 60 * 1000, timeoutDuration: 10_000 },
  )
  const keys: JWTVerifyGetKey = options.keys ?? remote!
  const issuer = [`https://sts.windows.net/${tenantId}/`, `https://login.microsoftonline.com/${tenantId}/v2.0`]

  const verify = async (authorization: string | undefined): Promise<Verified> => {
    const match = BEARER.exec(authorization ?? '')
    if (!match) return { ok: false, message: 'Sign in to ask the agents.' }
    try {
      const { payload } = await jwtVerify(match[1], keys, {
        issuer, audience: FOUNDRY_AUDIENCES, algorithms: ['RS256'], clockTolerance: 60,
        ...(options.currentDate ? { currentDate: options.currentDate } : {}),
      })
      const client = String(payload.appid ?? payload.azp ?? '').toLowerCase()
      const scopes = String(payload.scp ?? '').split(' ')
      if (String(payload.tid ?? '').toLowerCase() !== tenantId || client !== clientId || !scopes.includes('user_impersonation')
        || typeof payload.oid !== 'string') {
        return { ok: false, message: 'This sign-in is not for this app. Reload the page to sign in again.' }
      }
      return { ok: true, token: match[1] }
    } catch (error) {
      return { ok: false, message: rejected(error) }
    }
  }
  // Fetch the signing keys at startup, so the first question doesn't wait for them.
  const warmUp = async () => {
    await remote?.reload().catch(() => undefined)
  }
  return Object.assign(verify, { warmUp })
}
