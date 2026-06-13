// Patrón (a) del plan: la UI vive en el popup y pega contra el FastAPI local. El
// service worker (Chrome) / background script (Firefox) queda mínimo, listo para
// sumar Native Messaging (patrón b) más adelante sin reescribir el resto.
export default defineBackground(() => {
  console.debug('xport: background activo');
});
