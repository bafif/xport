// Cliente tipado del servicio FastAPI de xport. Los tipos son espejo de los
// schemas de `src/tweet_extractor/service/schemas.py`; mantenerlos en sync.

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

export interface ExportRequest {
  account: string;
  since: string; // YYYY-MM-DD
  until: string; // YYYY-MM-DD
}

export interface ExportResult {
  account: string;
  csv: string;
  download_url: string; // relativo a la base (p.ej. /exports/<file>)
  exported: number;
}

export interface ProgressResult {
  account: string;
  oldest: string | null; // YYYY-MM-DD del tweet más viejo capturado en el rango, o null
  captured: number; // cuántos tweets hay guardados en el rango
  oldest_count: number; // cuántos caen en el DÍA del más viejo (alto ⇒ día saturado)
}

export class XportApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'XportApiError';
  }
}

/** Habla con el FastAPI local. Se invoca desde el popup (una extension page): con
 *  `host_permissions` sobre el host, el fetch cross-origin no choca con CORS. */
export class XportClient {
  constructor(private readonly base: string) {}

  private async req<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${this.base}${path}`, {
        ...init,
        headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
      });
    } catch {
      throw new XportApiError(
        `no se pudo contactar al servicio en ${this.base} (¿está corriendo?)`,
        0,
      );
    }
    if (!res.ok) {
      throw new XportApiError(await detailOf(res), res.status);
    }
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
  progress(body: ExportRequest): Promise<ProgressResult> {
    return this.req<ProgressResult>('/progress', { method: 'POST', body: JSON.stringify(body) });
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
      return typeof detail === 'string' ? detail : JSON.stringify(detail);
    }
  } catch {
    /* sin body JSON */
  }
  return `${res.status} ${res.statusText}`;
}
