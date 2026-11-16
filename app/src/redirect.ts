// MSAL's redirect bridge (redirect.html): Entra sends every sign-in response here. After a sign-in or sign-out
// redirect the bridge returns to the page that started it; in the hidden iframe where MSAL renews a token silently, it
// hands the response to the app's page over a BroadcastChannel. It loads no other app code, so nothing touches the
// response first.
import { broadcastResponseToMainFrame } from '@azure/msal-browser/redirect-bridge'

broadcastResponseToMainFrame().catch((error: unknown) => {
  console.error('The sign-in response could not be handed to the app.', error)
})
