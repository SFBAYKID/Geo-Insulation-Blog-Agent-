/** Get a host-scoped preview cookie without sending the bypass secret to other sites. */
export async function previewCookies(url, secret = process.env.VERCEL_AUTOMATION_BYPASS_SECRET) {
  const parsed = new URL(url);
  if (parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost') return [];
  if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('.vercel.app') || parsed.username || parsed.password) {
    throw new Error('Expected a protected Vercel preview URL');
  }
  if (!secret) throw new Error('Configure the preview automation credential');
  const response = await fetch(url, {
    headers: {'x-vercel-protection-bypass': secret, 'x-vercel-set-bypass-cookie': 'true'},
    redirect: 'manual', signal: AbortSignal.timeout(30000),
  });
  const cookie = response.headers.getSetCookie().find(value => value.startsWith('_vercel_jwt='));
  if (!cookie || ![200, 302, 307].includes(response.status)) throw new Error('Preview authentication failed');
  return [{name: '_vercel_jwt', value: cookie.split(';')[0].slice('_vercel_jwt='.length),
    url: parsed.origin, httpOnly: true, secure: true, sameSite: 'Lax'}];
}
