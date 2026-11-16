import assert from 'node:assert/strict'
import test from 'node:test'
import { SignJWT, createLocalJWKSet, exportJWK, generateKeyPair, type JWTPayload } from 'jose'
import { createTokenVerifier } from '../server/auth.ts'

const tenant = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const client = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const now = new Date('2026-11-11T09:00:00Z')
const signing = await generateKeyPair('RS256')
const other = await generateKeyPair('RS256')
const jwk = { ...(await exportJWK(signing.publicKey)), kid: 'entra-key', alg: 'RS256' }
const verify = createTokenVerifier({ tenantId: tenant, clientId: client, keys: createLocalJWKSet({ keys: [jwk] }), currentDate: now })

// A v1 Foundry access token, as Entra issues one to the app's sign-in registration.
async function token(claims: JWTPayload = {}, key = signing.privateKey): Promise<string> {
  const seconds = Math.floor(now.getTime() / 1000)
  const { aud = 'https://ai.azure.com', ...rest } = claims
  return new SignJWT({ tid: tenant, appid: client, scp: 'user_impersonation', oid: '11111111-1111-4111-8111-111111111111', ...rest })
    .setProtectedHeader({ alg: 'RS256', kid: 'entra-key', typ: 'JWT' })
    .setIssuer(`https://sts.windows.net/${tenant}/`)
    .setAudience(aud)
    .setIssuedAt(seconds - 60)
    .setExpirationTime(seconds + 3600)
    .sign(key)
}

test('a Foundry token Entra issued to this app in this tenant is passed on', async () => {
  const value = await token()
  assert.deepEqual(await verify(`Bearer ${value}`), { ok: true, token: value })
  // v2 tokens name the client in azp and the resource by its ID.
  const v2 = await new SignJWT({ tid: tenant, azp: client, scp: 'user_impersonation', oid: '11111111-1111-4111-8111-111111111111' })
    .setProtectedHeader({ alg: 'RS256', kid: 'entra-key' })
    .setIssuer(`https://login.microsoftonline.com/${tenant}/v2.0`).setAudience('18a66f5f-dbdf-4c17-9dd7-1634712a9cbe')
    .setIssuedAt(Math.floor(now.getTime() / 1000)).setExpirationTime(Math.floor(now.getTime() / 1000) + 600).sign(signing.privateKey)
  assert.equal((await verify(`Bearer ${v2}`)).ok, true)
})

test('tokens for another app, tenant, audience or scope, or unsigned by Entra, are refused', async () => {
  const cases: [string, JWTPayload, CryptoKey?][] = [
    ['another client', { appid: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' }],
    ['another tenant', { tid: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' }],
    ['another audience', { aud: 'https://management.azure.com' }],
    ['no user_impersonation', { scp: 'Files.Read' }],
    ['no user', { oid: undefined }],
    ['another key', {}, other.privateKey],
  ]
  for (const [name, claims, key] of cases) {
    const result = await verify(`Bearer ${await token(claims, key)}`)
    assert.equal(result.ok, false, name)
  }
  const foreign = await new SignJWT({ tid: tenant, appid: client, scp: 'user_impersonation', oid: '11111111-1111-4111-8111-111111111111' })
    .setProtectedHeader({ alg: 'RS256', kid: 'entra-key' }).setIssuer('https://sts.windows.net/dddddddd-dddd-4ddd-8ddd-dddddddddddd/')
    .setAudience('https://ai.azure.com').setExpirationTime(Math.floor(now.getTime() / 1000) + 600).sign(signing.privateKey)
  assert.equal((await verify(`Bearer ${foreign}`)).ok, false, 'another issuer')
  const expired = await new SignJWT({ tid: tenant, appid: client, scp: 'user_impersonation', oid: 'x' })
    .setProtectedHeader({ alg: 'RS256', kid: 'entra-key' }).setIssuer(`https://sts.windows.net/${tenant}/`).setAudience('https://ai.azure.com')
    .setExpirationTime(Math.floor(now.getTime() / 1000) - 3600).sign(signing.privateKey)
  assert.deepEqual(await verify(`Bearer ${expired}`), { ok: false, message: 'Your sign-in expired. Reload the page to sign in again.' })
})

test('a missing or malformed header asks the user to sign in, without echoing it', async () => {
  for (const header of [undefined, '', 'Basic dXNlcjpwYXNz', 'Bearer not-a-jwt', `Bearer ${'a'.repeat(20)}.b.c extra`]) {
    const result = await verify(header)
    assert.equal(result.ok, false, String(header))
    assert.ok(!JSON.stringify(result).includes('dXNlcjpwYXNz'))
  }
})
