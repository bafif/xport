import { defineConfig } from 'wxt';

// WXT genera un build por navegador desde un solo código (docs: https://wxt.dev).
// Chrome → MV3 (service_worker); Firefox → su variante (background.scripts). El
// `browser` cross-browser sale de `wxt/browser` (capa polyfill), NO-OP en Firefox.
export default defineConfig({
  // Salidas: dist/chrome-mv3 y dist/firefox-mv2 (un subdir por target).
  outDir: 'dist',
  manifest: {
    name: 'xport — Extractor de tweets',
    description:
      'Dispara extracciones de tweets → CSV contra el servicio local de xport (FastAPI).',
    // `storage`: recuerda la URL base de la API. `activeTab`: al abrir el popup
    // (gesto del usuario) habilita mandarle start/stop del auto-scroll al content
    // script de la pestaña activa de x.com. `host_permissions`: deja que las páginas
    // de la extensión (popup/background) hagan fetch al servicio local sin chocar con
    // CORS (las extension pages con host_permissions no están sujetas a CORS).
    permissions: ['storage', 'activeTab'],
    host_permissions: ['http://localhost/*', 'http://127.0.0.1/*'],
  },
});
