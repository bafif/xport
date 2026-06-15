// Helpers puros de la captura GraphQL in-page (patrón C). Testeables sin DOM.
// La extensión NO parsea el tweet: solo detecta la operación y de qué cuenta es la
// búsqueda. El backend (mappers/) hace toda la interpretación.

export const CAPTURE_SOURCE = 'xport-capture' as const;

// URL base del servicio por default (un solo lugar para popup + background, así no
// divergen). `fastapi dev` sirve en :8000; para Docker/Nginx (:8080) se edita en el popup.
export const DEFAULT_BASE = 'http://localhost:8000';

// Claves de browser.storage.local compartidas entre popup y background.
export const STORAGE_BASE = 'xport:base'; // URL base del servicio
export const STORAGE_CAPTURE = 'xport:capture'; // captura on/off (default: on)
export const STORAGE_COUNTS = 'xport:captured'; // {account: tweets guardados}
export const STORAGE_AUTOSTART = 'xport:autostart'; // el popup lo prende; el content script
// de la pestaña de búsqueda recién abierta lo consume al cargar y arranca el auto-scroll solo

// Operaciones que capturamos. MVP: SearchTimeline (rango de fechas vía la búsqueda
// from:user since: until:). UserTweets* son timeline de perfil (otro envelope) — el
// backend hoy extrae SearchTimeline; las demás quedan para una fase siguiente.
export const CAPTURED_OPS = ['SearchTimeline', 'UserTweets', 'UserTweetsAndReplies'] as const;

const OP_RE = /\/(SearchTimeline|UserTweets|UserTweetsAndReplies)(\?|$)/;

export interface CaptureMsg {
  source: typeof CAPTURE_SOURCE;
  op: string;
  url: string;
  data: unknown;
}

/** El nombre de operación si la URL es un GraphQL capturable; `null` si no. Matchea
 *  por NOMBRE (no por queryId, que rota con cada deploy de x.com). */
export function matchOp(url: string): string | null {
  return url.match(OP_RE)?.[1] ?? null;
}

/** Extrae el handle de una rawQuery `from:user ...` (case-insensitive). */
export function accountFromQuery(rawQuery: string): string | null {
  return rawQuery.match(/from:(\w+)/i)?.[1] ?? null;
}

/** Deriva la cuenta de una URL de SearchTimeline: parsea el param `variables`
 *  (JSON) y busca `from:` en su `rawQuery`. `null` si no aplica (p.ej. UserTweets,
 *  que va por userId) o si la URL no trae la query. */
export function accountFromUrl(url: string): string | null {
  try {
    // x.com pide el GraphQL con URLs RELATIVAS (`/i/api/graphql/...`): hay que dar una
    // base, si no `new URL` tira y se perderían todas esas capturas. Solo leemos
    // searchParams, así que el host de la base no importa.
    const variables = new URL(url, 'https://x.com').searchParams.get('variables');
    if (!variables) return null;
    const parsed = JSON.parse(variables) as { rawQuery?: unknown };
    return typeof parsed.rawQuery === 'string' ? accountFromQuery(parsed.rawQuery) : null;
  } catch {
    return null;
  }
}
