/**
 * csrf.js — Interceptor global de fetch() para protección CSRF.
 *
 * Lee el token del meta tag <meta name="csrf-token"> inyectado por Jinja2
 * y lo agrega automáticamente como header X-CSRFToken en toda llamada
 * fetch() con método POST, PUT, PATCH o DELETE.
 *
 * No requiere modificar ninguna llamada fetch() existente.
 */
(function () {
  const CSRF_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  const metaTag = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = metaTag ? metaTag.getAttribute('content') : null;

  if (!csrfToken) {
    console.warn('[csrf.js] Meta tag csrf-token no encontrado — CSRF no activo en esta página.');
    return;
  }

  const _fetch = window.fetch.bind(window);

  window.fetch = function (input, init = {}) {
    const method = (init.method || 'GET').toUpperCase();

    if (CSRF_METHODS.has(method)) {
      init.headers = Object.assign({}, init.headers, {
        'X-CSRFToken': csrfToken,
      });
    }

    return _fetch(input, init);
  };
})();
