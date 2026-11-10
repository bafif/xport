// Cliente tipado del servicio FastAPI de xport. Los tipos son espejo de los
// schemas de `src/tweet_extractor/service/schemas.py`; mantenerlos en sync.

import type { Lang, Note } from './i18n';

export type JobStatus = 'pending' | 'running' | 'done' | 'error';

export interface JobCreate {
  accounts: string[];
  since: string; // YYYY-MM-DD (UTC, inclusiva)
  until: string; // YYYY-MM-DD (UTC, exclusiva)
  subwindow_days?: number | null;
  encoding?: string;
  delimiter?: string;
}

export interface AccountResultDTO {
  account: string;
  csv: string;
  download_url: string;
  exported: number;
  saved: number;
  windows_run: number;
  windows_skipped: number;
}

export interface JobResponse {
  id: string;
  status: JobStatus;
  accounts: string[];
  since: string;
  until: string;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  log: string[];
  results: AccountResultDTO[] | null;
}

export interface GateResponse {
  usage: number;
  remaining: number;
  hard_cap: number;
  window_s: number;
}

export interface ProgressRequest {
  account: string;
  since: string; // YYYY-MM-DD
  until: string; // YYYY-MM-DD
}

export interface ExportRequest extends ProgressRequest {
  encoding?: string; // default utf-8; `utf-8-sig` para Excel en castellano
  delimiter?: string; // default ','; ';' para Excel en castellano
}

/** El formato que lee bien un Excel configurado en castellano: UTF-8 con BOM y `;`. */
export const EXCEL_ES = { encoding: 'utf-8-sig', delimiter: ';' } as const;

export interface ExportResult {
  account: string;
  csv: string;
  download_url: string; // relativo a la base (p.ej. /exports/<file>)
  exported: number;
  excluded_replies: number; // capturados en el rango pero fuera del CSV (replies a terceros)
}

export interface ProgressResult {
  account: string;
  oldest: string | null; // ISO 8601 UTC, al segundo: el tweet más viejo capturado en el rango
  captured: number; // cuántos tweets hay guardados en el rango
}

export type CaptureState = 'queued' | 'running' | 'paused' | 'waiting' | 'done' | 'stopped';

export interface CaptureCreate {
  accounts: string[]; // una captura por cuenta
  since: string; // YYYY-MM-DD, inclusiva
  until: string; // YYYY-MM-DD, exclusiva
  encoding?: string;
  delimiter?: string;
}

/** Una captura de la cola (espejo de `CaptureOut`). */
export interface CaptureOut {
  id: number;
  account: string;
  since: string;
  until: string; // exclusiva
  state: CaptureState;
  note: { key: string; params?: Record<string, unknown> } | null; // un aviso del catálogo del servicio
  csv: string | null;
  download_url: string | null;
  exported: number | null;
  excluded_replies: number | null;
  oldest: string | null; // hasta qué día llegó
  captured: number;
  percent: number;
  last_page_at: string | null;
  resume_at: string | null; // esperando a X: cuándo sigue (ISO 8601)
}

export interface CaptureReport {
  state: 'running' | 'paused' | 'waiting' | 'done' | 'stopped';
  note?: Note; // el motivo, si se cortó: el servicio lo muestra en el idioma de quien mira
  resume_at?: string; // esperando a X: cuándo sigue (ISO 8601)
  by_user?: boolean; // la cortó la persona: el servicio no lo notifica
}

export interface ResetResult {
  account: string;
  deleted: number; // tweets borrados de la caché (el ledger del gate NO se toca)
}

/** Un pedido que falló. `status` 0: el servicio en `base` no contestó (el popup lo dice en
 *  su idioma); si no, `message` es lo que contestó el servicio, en el idioma pedido. */
export class XportApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly base?: string,
  ) {
    super(message);
    this.name = 'XportApiError';
  }
}

/** Habla con el FastAPI local. Se invoca desde el popup (una extension page): con
 *  `host_permissions` sobre el host, el fetch cross-origin no choca con CORS. */
export class XportClient {
  /** `runner`: la marca del navegador de xport, si esta extensión corre en él. `lang`: el
   *  idioma de los errores que contesta el servicio (va como `Accept-Language`). */
  constructor(
    private readonly base: string,
    private readonly runner?: string,
    private readonly lang?: Lang,
  ) {}

  private async req<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${this.base}${path}`, {
        ...init,
        headers: {
          'content-type': 'application/json',
          ...(this.lang ? { 'accept-language': this.lang } : {}),
          ...(init?.headers ?? {}),
        },
      });
    } catch {
      throw new XportApiError(`${this.base} unreachable`, 0, this.base);
    }
    if (!res.ok) {
      throw new XportApiError(await detailOf(res), res.status);
    }
    if (res.status === 204) return null as T;
    return (await res.json()) as T;
  }

  createJob(body: JobCreate): Promise<JobResponse> {
    return this.req<JobResponse>('/jobs', { method: 'POST', body: JSON.stringify(body) });
  }

  getJob(id: string): Promise<JobResponse> {
    return this.req<JobResponse>(`/jobs/${encodeURIComponent(id)}`);
  }

  gate(): Promise<GateResponse> {
    return this.req<GateResponse>('/gate');
  }

  /** Exporta a CSV lo capturado in-page de una cuenta en un rango (patrón C). */
  exportCapture(body: ExportRequest): Promise<ExportResult> {
    return this.req<ExportResult>('/export', { method: 'POST', body: JSON.stringify(body) });
  }

  /** Frente de reanudación: hasta dónde (tweet más viejo) se capturó una cuenta en un
   *  rango. La captura retoma desde ahí en vez de re-pedir desde `until`. */
  progress(body: ProgressRequest): Promise<ProgressResult> {
    return this.req<ProgressResult>('/progress', { method: 'POST', body: JSON.stringify(body) });
  }

  /** Borra del backend TODO lo capturado de una cuenta: resetea el frente de
   *  reanudación de `/progress` (un tweet viejo colado lo deja trabado y la captura
   *  no vuelve a traer los nuevos). NO toca el ledger del gate. */
  resetAccount(account: string): Promise<ResetResult> {
    return this.req<ResetResult>('/reset', { method: 'POST', body: JSON.stringify({ account }) });
  }

  /** Encola una captura por cuenta (la misma cuenta y rango en cola no se repite). */
  enqueue(body: CaptureCreate): Promise<CaptureOut[]> {
    return this.req<CaptureOut[]>('/captures', { method: 'POST', body: JSON.stringify(body) });
  }

  captures(): Promise<CaptureOut[]> {
    return this.req<CaptureOut[]>('/captures');
  }

  /** La captura que va: la que está en curso o la siguiente de la cola, que el servicio
   *  pasa a en curso. `null` si no hay nada (o si el tope de 24 h está lleno). */
  nextCapture(): Promise<CaptureOut | null> {
    const headers: Record<string, string> = this.runner ? { 'x-xport-runner': this.runner } : {};
    return this.req<CaptureOut | null>('/captures/next', { method: 'POST', headers });
  }

  captureState(id: number, body: CaptureReport): Promise<CaptureOut> {
    return this.req<CaptureOut>(`/captures/${id}/state`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  /** URL absoluta de descarga a partir del `download_url` relativo del backend. */
  absolute(path: string): string {
    return `${this.base}${path}`;
  }

  /** URL de descarga del CSV de una cuenta (FileResponse con Content-Disposition). */
  csvUrl(id: string, account: string): string {
    return `${this.base}/jobs/${encodeURIComponent(id)}/csv/${encodeURIComponent(account)}`;
  }
}

/** Extrae el `detail` de un error de FastAPI; cae al status si no hay body JSON. */
async function detailOf(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        // 422: los mensajes de validación, ya en el idioma pedido
        return detail.map((d: { msg?: unknown }) => String(d.msg ?? '')).join('. ');
      }
      return JSON.stringify(detail);
    }
  } catch {
    /* sin body JSON */
  }
  return `${res.status} ${res.statusText}`;
}
