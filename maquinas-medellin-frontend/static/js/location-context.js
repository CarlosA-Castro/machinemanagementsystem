/**
 * location-context.js
 * Maneja la barra de contexto de local activo en todas las vistas admin.
 *
 * Incluir al final del <body> con:
 *   <script src="{{ url_for('static', filename='js/location-context.js') }}"></script>
 */

const LocationContext = (() => {
  let _ctx = null;
  let _locales = null;

  function _setNombreLocal(texto) {
    const nombreEl = document.getElementById('lcb-nombre-local');
    if (nombreEl) nombreEl.textContent = texto;
  }

  async function init() {
    try {
      const res = await fetch('/api/session/contexto-local');
      if (!res.ok) {
        _setNombreLocal('No disponible');
        return;
      }
      _ctx = await res.json();
      _renderBar();
    } catch (e) {
      _setNombreLocal('No disponible');
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

    if (!active_location_id && can_view_all_locations) {
      _setNombreLocal('Todos los locales');
    } else {
      _setNombreLocal(active_location_name || '—');
    }

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

    const btnCambiar = document.getElementById('lcb-btn-cambiar');
    const elFijo = document.getElementById('lcb-fijo');

    if (can_switch_location) {
      if (btnCambiar) {
        btnCambiar.classList.remove('hidden');
        btnCambiar.classList.add('flex');
      }
      if (elFijo) {
        elFijo.classList.add('hidden');
        elFijo.classList.remove('flex');
      }
    } else {
      if (btnCambiar) {
        btnCambiar.classList.add('hidden');
        btnCambiar.classList.remove('flex');
      }
      if (elFijo) {
        elFijo.classList.remove('hidden');
        elFijo.classList.add('flex');
      }
    }
  }

  async function toggleDropdown() {
    const dropdown = document.getElementById('lcb-dropdown');
    if (!dropdown) return;

    const isOpen = !dropdown.classList.contains('hidden');
    if (isOpen) {
      cerrarDropdown();
      return;
    }

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
      if (!res.ok) {
        _locales = [];
        return;
      }
      const data = await res.json();
      _locales = data.locales || [];
    } catch (e) {
      console.warn('[LocationContext] Error cargando locales:', e);
      _locales = [];
    }
  }

  function _renderDropdown() {
    const lista = document.getElementById('lcb-dropdown-lista');
    if (!lista) return;

    const activeId = _ctx && _ctx.active_location_id;

    if (!_locales || !_locales.length) {
      lista.innerHTML = `
        <div class="px-4 py-3 text-sm text-gray-500">
          No hay locales disponibles para esta sesión.
        </div>
      `;
    } else {
      lista.innerHTML = _locales.map((local) => {
        const isActive = local.id === activeId;
        const safeName = String(local.name || '').replace(/'/g, "\\'");
        return `
          <button
            onclick="LocationContext.cambiarLocal(${local.id}, '${safeName}')"
            class="w-full flex items-center gap-2 px-4 py-3 text-sm text-left transition-colors
                   ${isActive ? 'bg-blue-50 text-blue-700 font-semibold cursor-default' : 'text-gray-700 hover:bg-gray-50'}"
            ${isActive ? 'disabled' : ''}>
            <i class="fas fa-store text-xs ${isActive ? 'text-blue-500' : 'text-gray-400'}"></i>
            ${local.name}
            ${isActive ? '<i class="fas fa-check text-xs text-blue-500 ml-auto"></i>' : ''}
          </button>
        `;
      }).join('');
    }

    const dropdownTodos = document.getElementById('lcb-dropdown-todos');
    if (dropdownTodos) {
      if (_ctx && _ctx.can_view_all_locations) {
        dropdownTodos.classList.remove('hidden');
      } else {
        dropdownTodos.classList.add('hidden');
      }
    }
  }

  async function cambiarLocal(locationId, locationName) {
    cerrarDropdown();
    _setNombreLocal(`Cambiando a ${locationName}...`);

    try {
      const res = await fetch('/api/session/seleccionar-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location_id: locationId }),
      });
      const data = await res.json();

      if (data.ok) {
        _locales = null;
        _ctx = null;
        window.location.reload();
      } else {
        if (_ctx) _setNombreLocal(_ctx.active_location_name || '—');
        _mostrarToast('No se pudo cambiar el local.', 'error');
      }
    } catch (e) {
      if (_ctx) _setNombreLocal(_ctx.active_location_name || '—');
      _mostrarToast('Error de conexión.', 'error');
    }
  }

  async function verTodos() {
    cerrarDropdown();
    _setNombreLocal('Cargando todos los locales...');

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

  function _mostrarToast(mensaje, tipo) {
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 z-[9999] px-4 py-3 rounded-lg shadow-lg text-white text-sm ${
      tipo === 'error' ? 'bg-red-500' : 'bg-green-500'
    }`;
    toast.textContent = mensaje;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  return { init, toggleDropdown, cerrarDropdown, cambiarLocal, verTodos };
})();

document.addEventListener('DOMContentLoaded', () => LocationContext.init());
