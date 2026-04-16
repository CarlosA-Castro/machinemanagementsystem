/**
 * location-context.js
 * Maneja la barra de contexto de local activo en todas las vistas admin.
 *
 * Incluir al final del <body> con:
 *   <script src="{{ url_for('static', filename='js/location-context.js') }}"></script>
 */

const LocationContext = (() => {

  let _ctx = null;        // contexto cargado desde /api/session/contexto-local
  let _locales = null;    // lista cargada desde /api/session/locales-disponibles

  // ── Inicialización ──────────────────────────────────────────────────────────

  async function init() {
    try {
      const res = await fetch('/api/session/contexto-local');
      if (!res.ok) return;
      _ctx = await res.json();
      _renderBar();
    } catch (e) {
      console.warn('[LocationContext] No se pudo cargar contexto:', e);
    }
  }

  function _renderBar() {
    if (!_ctx) return;

    const {
      active_location_id,
      active_location_name,
      can_switch_location,
      can_view_all_locations,
    } = _ctx;

    // Nombre del local
    const nombreEl = document.getElementById('lcb-nombre-local');
    if (nombreEl) {
      if (!active_location_id && can_view_all_locations) {
        nombreEl.textContent = 'Todos los locales';
      } else {
        nombreEl.textContent = active_location_name || '—';
      }
    }

    // Badge "Todos los locales"
    const badgeTodos = document.getElementById('lcb-badge-todos');
    if (badgeTodos) {
      if (!active_location_id && can_view_all_locations) {
        badgeTodos.classList.remove('hidden');
        badgeTodos.classList.add('flex');
      } else {
        badgeTodos.classList.add('hidden');
        badgeTodos.classList.remove('flex');
      }
    }

    // Botón "Ver todos" (solo visible si can_view_all Y hay local activo)
    const btnTodos = document.getElementById('lcb-btn-todos');
    if (btnTodos) {
      if (can_view_all_locations && active_location_id) {
        btnTodos.classList.remove('hidden');
        btnTodos.classList.add('flex');
      } else {
        btnTodos.classList.add('hidden');
        btnTodos.classList.remove('flex');
      }
    }

    // Controles de cambio de local
    const btnCambiar = document.getElementById('lcb-btn-cambiar');
    const elFijo     = document.getElementById('lcb-fijo');

    if (can_switch_location) {
      if (btnCambiar) { btnCambiar.classList.remove('hidden'); btnCambiar.classList.add('flex'); }
      if (elFijo)     { elFijo.classList.add('hidden'); elFijo.classList.remove('flex'); }
    } else {
      if (btnCambiar) { btnCambiar.classList.add('hidden'); btnCambiar.classList.remove('flex'); }
      if (elFijo)     { elFijo.classList.remove('hidden'); elFijo.classList.add('flex'); }
    }
  }

  // ── Dropdown de locales ─────────────────────────────────────────────────────

  async function toggleDropdown() {
    const dropdown = document.getElementById('lcb-dropdown');
    if (!dropdown) return;

    const isOpen = !dropdown.classList.contains('hidden');
    if (isOpen) {
      cerrarDropdown();
      return;
    }

    // Cargar lista de locales si aún no está en memoria
    if (!_locales) {
      await _cargarLocales();
    }
    _renderDropdown();
    dropdown.classList.remove('hidden');

    const overlay = document.getElementById('lcb-overlay');
    if (overlay) overlay.classList.remove('hidden');

    const chevron = document.getElementById('lcb-chevron');
    if (chevron) chevron.style.transform = 'rotate(180deg)';
  }

  function cerrarDropdown() {
    const dropdown = document.getElementById('lcb-dropdown');
    if (dropdown) dropdown.classList.add('hidden');

    const overlay = document.getElementById('lcb-overlay');
    if (overlay) overlay.classList.add('hidden');

    const chevron = document.getElementById('lcb-chevron');
    if (chevron) chevron.style.transform = '';
  }

  async function _cargarLocales() {
    try {
      const res = await fetch('/api/session/locales-disponibles');
      if (!res.ok) return;
      const data = await res.json();
      _locales = data.locales || [];
    } catch (e) {
      console.warn('[LocationContext] Error cargando locales:', e);
      _locales = [];
    }
  }

  function _renderDropdown() {
    const lista = document.getElementById('lcb-dropdown-lista');
    if (!lista || !_locales) return;

    const activeId = _ctx && _ctx.active_location_id;

    lista.innerHTML = _locales.map(l => {
      const isActive = l.id === activeId;
      return `
        <button
          onclick="LocationContext.cambiarLocal(${l.id}, '${l.name.replace(/'/g, "\\'")}')"
          class="w-full flex items-center gap-2 px-4 py-3 text-sm text-left transition-colors
                 ${isActive
                   ? 'bg-blue-50 text-blue-700 font-semibold cursor-default'
                   : 'text-gray-700 hover:bg-gray-50'
                 }"
          ${isActive ? 'disabled' : ''}>
          <i class="fas fa-store text-xs ${isActive ? 'text-blue-500' : 'text-gray-400'}"></i>
          ${l.name}
          ${isActive ? '<i class="fas fa-check text-xs text-blue-500 ml-auto"></i>' : ''}
        </button>
      `;
    }).join('');

    // Mostrar/ocultar opción "Ver todos" dentro del dropdown
    const dropdownTodos = document.getElementById('lcb-dropdown-todos');
    if (dropdownTodos && _ctx && _ctx.can_view_all_locations) {
      dropdownTodos.classList.remove('hidden');
    }
  }

  // ── Cambio de local ─────────────────────────────────────────────────────────

  async function cambiarLocal(locationId, locationName) {
    cerrarDropdown();

    // Feedback visual inmediato
    const nombreEl = document.getElementById('lcb-nombre-local');
    if (nombreEl) nombreEl.textContent = 'Cambiando...';

    try {
      const res = await fetch('/api/session/seleccionar-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location_id: locationId }),
      });
      const data = await res.json();

      if (data.ok) {
        // Invalidar caché de locales y recargar contexto
        _locales = null;
        _ctx = null;
        // Recargar la página para que todos los datos se filtren por el nuevo local
        window.location.reload();
      } else {
        if (nombreEl && _ctx) nombreEl.textContent = _ctx.active_location_name || '—';
        _mostrarToast('No se pudo cambiar el local.', 'error');
      }
    } catch (e) {
      if (nombreEl && _ctx) nombreEl.textContent = _ctx.active_location_name || '—';
      _mostrarToast('Error de conexión.', 'error');
    }
  }

  async function verTodos() {
    cerrarDropdown();

    const nombreEl = document.getElementById('lcb-nombre-local');
    if (nombreEl) nombreEl.textContent = 'Cargando...';

    try {
      const res = await fetch('/api/session/seleccionar-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location_id: null }),
      });
      const data = await res.json();

      if (data.ok) {
        _locales = null;
        _ctx = null;
        window.location.reload();
      } else {
        _mostrarToast('No se pudo activar vista global.', 'error');
        init();
      }
    } catch (e) {
      _mostrarToast('Error de conexión.', 'error');
      init();
    }
  }

  // ── Utilidades ──────────────────────────────────────────────────────────────

  function _mostrarToast(mensaje, tipo) {
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 z-[9999] px-4 py-3 rounded-lg shadow-lg text-white text-sm
      ${tipo === 'error' ? 'bg-red-500' : 'bg-green-500'}`;
    toast.textContent = mensaje;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  // ── API pública ─────────────────────────────────────────────────────────────
  return { init, toggleDropdown, cerrarDropdown, cambiarLocal, verTodos };

})();

// Auto-inicializar cuando el DOM esté listo
document.addEventListener('DOMContentLoaded', () => LocationContext.init());
