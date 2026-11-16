// Browser-only sign-in with MSAL: the user's own token for Foundry (https://ai.azure.com), requested by this app's
// single-page registration with PKCE and sent only to the app's own server, which passes it to Foundry for this
// user's turns. It's the only token the app requests: Foundry asks each user once for consent to Fabric IQ or Work IQ,
// through the link the app shows. live/client.ts loads this module only outside mock mode, so Node tests and the mock
// build never include it. Entra returns every response to MSAL's redirect bridge (REDIRECT_PATH), which hands it to
// this page.
import { InteractionRequiredAuthError, PublicClientApplication, type AccountInfo } from '@azure/msal-browser'
import { REDIRECT_PATH, type PublicConfig } from './protocol.ts'
import type { Identity } from './types.ts'

let session: { app: PublicClientApplication; scopes: string[] } | undefined

function identityOf(account: AccountInfo): Identity {
  return { name: account.name || account.username }
}

function current(): { app: PublicClientApplication; scopes: string[] } {
  if (!session) throw new Error('Sign-in has not started yet. Reload the page.')
  return session
}

// Starts MSAL and finishes a sign-in that redirected back here. Resolves with the signed-in user, or undefined when
// nobody has signed in in this tab yet.
export async function startSignIn(config: PublicConfig): Promise<Identity | undefined> {
  const redirectUri = `${window.location.origin}${REDIRECT_PATH}`
  const app = new PublicClientApplication({
    auth: {
      clientId: config.clientId,
      authority: `https://login.microsoftonline.com/${config.tenantId}`,
      redirectUri,
      postLogoutRedirectUri: redirectUri,
    },
    cache: { cacheLocation: 'sessionStorage' },
  })
  await app.initialize()
  const redirected = await app.handleRedirectPromise()
  session = { app, scopes: config.scopes }
  const account = redirected?.account ?? app.getActiveAccount()
    ?? app.getAllAccounts().find((candidate) => candidate.tenantId === config.tenantId)
  if (!account) return undefined
  app.setActiveAccount(account)
  return identityOf(account)
}

// Redirects to Microsoft Entra sign-in; the page comes back signed in.
export async function signIn(): Promise<void> {
  const { app, scopes } = current()
  await app.loginRedirect({ scopes })
}

export async function signOut(): Promise<void> {
  const { app } = current()
  await app.logoutRedirect({ account: app.getActiveAccount() })
}

// A fresh Foundry token for the signed-in user. MSAL refreshes it silently with the refresh token and, once that
// expires a day after sign-in, in a hidden iframe that Entra returns to the redirect bridge. When Entra needs the
// user again, such as for a new consent, the page redirects to sign-in and the question can be asked again afterwards.
export async function accessToken(): Promise<string> {
  const { app, scopes } = current()
  const account = app.getActiveAccount()
  if (!account) throw new Error('Sign in to ask the agents.')
  try {
    return (await app.acquireTokenSilent({ scopes, account })).accessToken
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await app.acquireTokenRedirect({ scopes, account })
      throw new Error('Microsoft Entra needs you to sign in again; the page is redirecting.', { cause: error })
    }
    throw error
  }
}
