# Scenario assistant app

A one-page React app and its Node.js server, deployed as one container on Azure Container Apps. The browser signs
the user in with MSAL (PKCE) and asks one of the two Foundry prompt agents, **with IQ** or **without IQ**, or both
side by side. The server verifies the user's Foundry token, then calls the configured project's Responses API as that
user and streams each tool call and the answer back. It holds no secret and stores no answer.

## Layout

| Path | What it holds |
| --- | --- |
| `src/` | The page: `App.tsx`, `components/`, the sample questions (`samples.ts`) and the conversation state (`exchanges.ts`) |
| `src/demo/` | The demo script (`script.json`), the single source of the sample questions, presenter mode, mock mode's replies and the rehearsal; its validation (`script.ts`) and presenter mode's rules (`presenter.ts`) |
| `src/live/` | The agent layer: wire protocol, stream reader, one turn, the reply parser and safe Markdown renderer, MSAL sign-in, the client |
| `src/mock/` | Mock mode, which replays the demo script's scripted answers, loaded only behind `import.meta.env.MODE === 'mock'`, so production builds leave it out |
| `src/redirect.ts`, `redirect.html` | MSAL's redirect bridge, the registration's only redirect URI |
| `server/` | Settings (`config.ts`), token verification (`auth.ts`), the turn route (`turn.ts`), the Foundry stream (`foundry.ts`), static files, tracing, the `main.ts` and `dev.ts` entries, and the rehearsal (`rehearsal.ts`, `rehearse.ts`), which the image doesn't include |
| `tests/` | `node --test` with type stripping: server, protocol, parser, renderer, turn, demo script, presenter mode, rehearsal, mock and bundle checks |

## Commands

From `app/`:

| Command | Runs |
| --- | --- |
| `npm run dev:mock` | The page at `http://localhost:3000/` with the demo script's scripted answers: no sign-in, no Azure |
| `npm run dev` | The real server with Vite's middleware at `http://localhost:3000/`, against the deployment `.env.local` names |
| `npm run build` | Type checks, then builds the page into `dist/` and bundles the server into one module, `dist-server/main.mjs` |
| `npm start` | The built server on port 8080 |
| `npm run lint`, `npm test` | ESLint; the unit tests |
| `npm run rehearse` | Asks the demo script's questions of the agents that `.env.local` names, as the Azure CLI's user, and checks each answer: a live operation ([rehearsal](../docs/development.md#demo-script-presenter-mode-and-rehearsal)) |

## Run it against a deployment

Write the app settings to the ignored `app/.env.local`, from the repository root:

```sh
terraform -chdir=infra output -json app_settings \
  | jq -r 'to_entries[] | "\(.key)=\(.value)"' > app/.env.local
```

Sign-in works only where the app registration lists the redirect bridge. For `npm run dev`, add
`app_local_redirect_uris = ["http://localhost:3000/redirect.html"]` to your tfvars and rerun the deployment
(`deploy.py up`), which adds it to the registration. In a Codespace, open port 3000 through VS Code's local
forwarding so the browser shows `localhost:3000`; keep ports private.

## Presenter mode

Add `?presenter=1` to the app's URL, or select **Presenter mode** in the header: the demo script's
steps appear on a rail, with the current step's question, notes and timers. Arrow keys, Page Up
and Page Down, Enter, N and B work as a presentation clicker sends them. When a step's turn ends
without an answer, **Show scripted answer** shows the script's reply, labelled *Scripted answer,
not live*. It works in mock mode too. The [presenter runbook](../docs/runbook.md) has the
procedure.

## Settings

| Variable | Value |
| --- | --- |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>`: the only endpoint the server calls with a user's token |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` | The tenant and the app's single-page registration; tokens must carry both |
| `AGENT_WITH_IQ`, `AGENT_WITHOUT_IQ` | The two Foundry agents' names |
| `PORT` | Optional, 8080 (`npm run dev`: 3000) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_CLIENT_ID`, `APP_VERSION` | Optional, set on the container app: traces go to Application Insights through the app's managed identity, never an ingestion key |
| `TURN_TIMEOUT_MS` | Optional, 225000: inside Container Apps' 240-second request limit |

An invalid or incomplete configuration stops the server at start.

## Routes

- `GET /healthz`: `ok`, untraced.
- `GET /api/config`: `{ tenantId, clientId, scopes }`, the public sign-in settings.
- `POST /api/turns`: `{ cid, variant, question, previousResponseId? }` with the user's bearer token: a lowercase
  UUID, `with_iq` or `without_iq`, 1 to 2000 characters, and Foundry's response ID for a follow-up. Anything else is
  a 400; a missing or invalid token is a 401 before anything else is read. The answer streams as newline-delimited
  JSON: `{ "type": "tool", "call": … }` as each tool starts and finishes, `{ "type": "heartbeat" }` every 15
  seconds, then `{ "type": "result", "outcome": … }`, which is `answered` (Markdown text), `needs-consent` (a link
  to authorize Fabric IQ or Work IQ) or `failed`, each with Foundry's response ID when there is one.
- Everything else: the built page, with a strict content security policy. Only `/redirect.html` may be framed, by
  the app itself.

## Image

`Dockerfile` builds from `app/` as the context: Node 24 builds the page and the server bundle, and a distroless,
non-root Node 24 image runs only `dist/` and `dist-server/`, without `node_modules`, on port 8080. Where your network
blocks `registry.npmjs.org`, add `--build-arg NPM_REGISTRY=<npm mirror URL>`.

```sh
docker build --tag scenario-assistant app
docker run --rm --publish 3000:8080 --env-file app/.env.local scenario-assistant
```

Keep `package-lock.json` on `https://registry.npmjs.org/` URLs; CI rejects other hosts
([npm lockfile](../CONTRIBUTING.md#npm-lockfile)).
