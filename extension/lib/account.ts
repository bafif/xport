// Por qué una captura no trajo ni un tweet (punto 10 de docs/PLAN.md). Puro y testeable.
//
// Cuando una captura de la cola termina sin un solo tweet guardado en el rango, el background
// lleva su pestaña a `x.com/<cuenta>/following` antes de avisar que terminó. Esa página pide
// `UserByScreenName`, que dice si la cuenta existe, está suspendida o es protegida, y no carga
// ningún post (medido el 11/11/2026: el perfil, en cambio, carga sus últimos tweets, que el
// tope tendría que contar). La extensión manda la respuesta cruda con el `done` y no la
// interpreta: lo hace mappers/ (regla 4).

/** Clave de `browser.storage.local` con la comprobación en curso (o la última, terminada). */
export const STORAGE_CHECK = 'xport:check';

// Si x.com no contesta en este plazo, la captura termina con el aviso de siempre.
export const CHECK_TIMEOUT_MS = 45_000;

export interface AccountCheck {
  captureId: number;
  account: string; // en minúsculas, como la guarda el servicio
  tabId: number; // la pestaña de la captura, que hace la comprobación
  startedAt: number; // ms
  finished?: boolean; // ya se avisó el `done`: la pestaña queda para la próxima captura
}

/** La página que pide `UserByScreenName` sin cargar tweets. */
export function checkUrl(account: string): string {
  return `https://x.com/${encodeURIComponent(account)}/following`;
}

/** ¿Sigue la pestaña donde la dejó la comprobación? En la página de seguidos, o en el perfil
 *  al que x.com redirige con una cuenta protegida. Si la persona la usó para otra cosa (o, tras
 *  reiniciar el navegador, ese id es de otra pestaña), no se reusa. */
export function onCheckPage(href: string, account: string): boolean {
  if (!account) return false;
  try {
    const u = new URL(href);
    if (!/(^|\.)(x|twitter)\.com$/.test(u.hostname)) return false;
    const path = u.pathname.replace(/\/+$/, '').toLowerCase();
    return path === `/${account}` || path === `/${account}/following`;
  } catch {
    return false;
  }
}

/** Qué hace la vuelta de la cola con la comprobación guardada: esperarla (la cola no avanza),
 *  darla por terminada sin respuesta (pasó el plazo) o nada (no hay, o ya terminó). */
export function checkStep(
  check: AccountCheck | null,
  now: number,
): 'none' | 'waiting' | 'expired' {
  if (!check || check.finished) return 'none';
  return now - check.startedAt < CHECK_TIMEOUT_MS ? 'waiting' : 'expired';
}

/** ¿Es la respuesta de `UserByScreenName` que espera la comprobación? La de cualquier otra
 *  cuenta (una tarjeta al pasar el mouse, el navegador de todos los días) no cuenta. */
export function answersCheck(
  check: AccountCheck | null,
  screenName: string | null,
): check is AccountCheck {
  return check !== null && !check.finished && screenName !== null && screenName === check.account;
}

/** Lo que va como `user` en el `done`: la respuesta tal cual, si es un objeto JSON (el servicio
 *  no acepta otra cosa, y un `done` rechazado dejaría la captura en curso). */
export function userPayload(data: unknown): Record<string, unknown> | undefined {
  return data !== null && typeof data === 'object' && !Array.isArray(data)
    ? (data as Record<string, unknown>)
    : undefined;
}
