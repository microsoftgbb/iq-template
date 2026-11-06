import { InteractionRequiredAuthError, PublicClientApplication } from '@azure/msal-browser'
import type { AppConfig } from './config.ts'

export interface Auth {
  user?: string
  signIn(): Promise<void>
  signOut(): Promise<void>
  token(): Promise<string>
}

// Public SPA client with PKCE: the browser holds no secret and calls the agent as the signed-in user.
export async function createAuth(config: AppConfig): Promise<Auth> {
  const client = new PublicClientApplication({
    auth: {
      clientId: config.clientId,
      authority: `https://login.microsoftonline.com/${config.tenantId}`,
      redirectUri: `${window.location.origin}/`,
    },
    cache: { cacheLocation: 'sessionStorage' },
  })
  await client.initialize()
  const redirect = await client.handleRedirectPromise()
  const account = redirect?.account ?? client.getActiveAccount() ?? client.getAllAccounts()[0]
  if (account) client.setActiveAccount(account)
  const scopes = [config.scope]

  return {
    user: account?.username,
    signIn: () => client.loginRedirect({ scopes }),
    signOut: () => client.logoutRedirect({ account: client.getActiveAccount() ?? undefined }),
    async token() {
      const active = client.getActiveAccount()
      if (!active) {
        await client.loginRedirect({ scopes })
        throw new Error('Redirecting to sign in.')
      }
      try {
        return (await client.acquireTokenSilent({ scopes, account: active })).accessToken
      } catch (error) {
        if (!(error instanceof InteractionRequiredAuthError)) throw error
        await client.acquireTokenRedirect({ scopes, account: active })
        throw new Error('Redirecting to sign in.', { cause: error })
      }
    },
  }
}
