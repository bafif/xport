import { defineConfig } from 'wxt';

// WXT genera un build por navegador desde un solo código (docs: https://wxt.dev).
// Chrome → MV3 (service_worker); Firefox → su variante (background.scripts). El
// `browser` cross-browser sale de `wxt/browser` (capa polyfill), NO-OP en Firefox.
const CHROME_KEY =
  'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArLjBfADjCfE+EMBnSsFR+x9pTL9PqUsoIE1PngxLUHZ0JdavLWk6PhwVQpmvEVtd/Z9qMvcEO3+4ZN2houiCDYvLrm+dt0oWuqEfvzzNOlMFoscXDxzjmlFp4xrevjHtQg2SQ5gXnudRFeDLQT6rH+MHHLHnAQsWoVf9kkTOpXaI9aQdTRJNZMiCIDimhnbvlwSlsXlF/lIdPWI5+pGFN4oHujpTGgXrLoz2z8YF9x6p9cknt7FrdZGzfEXMNh7DQDCEOpk0eKoQv3oYR5fcbfYTv59MWIZ4OfJjpyx05+tXLqe7DqIihFTywa/v8QfhxkgnmD0SjWHAz2b5BXDciwIDAQAB';

export default defineConfig({
  // Salidas: dist/chrome-mv3 y dist/firefox-mv2 (un subdir por target).
  outDir: 'dist',
  // Una función: la `key` va solo en el build de Chrome (Firefox la marcaría como desconocida).
  manifest: ({ browser }) => ({
    name: 'xport — Extractor de tweets',
    description:
      'Dispara extracciones de tweets → CSV contra el servicio local de xport (FastAPI).',
    // `storage`: recuerda la URL base de la API. `activeTab`: al abrir el popup
    // (gesto del usuario) habilita mandarle start/stop del auto-scroll al content
    // script de la pestaña activa de x.com. `nativeMessaging`: deja que el background
    // lance el supervisor local (deploy/native-host) que arranca/baja el FastAPI solo,
    // así no hace falta correr `uvicorn` a mano. `host_permissions`: deja que las
    // páginas de la extensión (popup/background) hagan fetch al servicio local sin
    // chocar con CORS (las extension pages con host_permissions no están sujetas a CORS).
    // `alarms`: despierta al background cada medio minuto para mirar la cola de capturas.
    permissions: ['storage', 'activeTab', 'nativeMessaging', 'alarms'],
    host_permissions: ['http://localhost/*', 'http://127.0.0.1/*'],
    // Id estable de Firefox: el manifest del native host lo lista en `allowed_extensions`
    // (debe coincidir con install-linux.sh / install-windows.ps1, default `xport@local`).
    browser_specific_settings: {
      gecko: { id: 'xport@local' },
    },
    // Id estable de Chrome, `aiildgjidjmeilmjinaililpijenmodi`: sale de esta clave pública, así no cambia
    // según la carpeta desde la que se carga la extensión y el native host la autoriza sin pedir
    // el id a mano (`allowed_origins` en install-linux.sh). La clave privada no se guarda: solo
    // serviría para empaquetar un .crx, y la extensión no se publica en la Chrome Web Store.
    ...(browser === 'chrome' ? { key: CHROME_KEY } : {}),
  }),
});
