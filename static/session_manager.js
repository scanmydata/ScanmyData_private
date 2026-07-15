/**
 * Session Manager - Automatic logout on inactivity or tab close
 * 
 * Features:
 * - Detects user inactivity (>30 minutes)
 * - Logs out user when closing browser/tab
 * - Uses local storage to detect multi-tab sessions
 */

(function() {
  'use strict';

  const serverTimeoutSeconds = Number(window.SESSION_TIMEOUT_SECONDS || 0);
  const resolvedTimeoutMs = Number.isFinite(serverTimeoutSeconds) && serverTimeoutSeconds > 0
    ? serverTimeoutSeconds * 1000
    : 10 * 60 * 1000;

  const serverPingSeconds = Number(window.TAB_PING_INTERVAL_SECONDS || 0);
  const resolvedPingMs = Number.isFinite(serverPingSeconds) && serverPingSeconds > 0
    ? serverPingSeconds * 1000
    : 25 * 1000;

  // Configuration
  const CONFIG = {
    INACTIVITY_TIMEOUT: resolvedTimeoutMs,
    CHECK_INTERVAL: 5 * 1000, // Check every 5 seconds
    LOGOUT_WARNING_TIME: Math.min(30 * 1000, Math.max(10 * 1000, Math.floor(resolvedTimeoutMs * 0.2))),
    STORAGE_KEY: 'fbp_session_activity',
    STORAGE_KEY_TAB: 'fbp_session_tab_id',
    // auth_bp is registered with no url_prefix (app.py), so its routes live at the
    // root -- '/auth/api/logout' never existed and every inactivity logout 404'd.
    LOGOUT_ENDPOINT: '/api/logout',
    // Says "this tab is still open" -- see startTabPing().
    PING_ENDPOINT: '/api/session/ping',
    PING_INTERVAL: resolvedPingMs
  };

  // Session state
  let lastActivityTime = Date.now();
  let activityCheckInterval = null;
  let hasShownWarning = false;
  let tabId = null;
  let isLoggingOut = false;

  /**
   * Initialize session manager
   */
  const IS_LOGGED_IN = window.IS_LOGGED_IN === true || window.IS_LOGGED_IN === 'true';

  function init() {
    if (!IS_LOGGED_IN) {
      console.info('SessionManager disabled for unauthenticated users.');
      return;
    }

    // Generate unique tab ID
    tabId = generateTabId();
    
    // Set initial activity time
    updateActivityTime();

    // Attach activity listeners
    attachActivityListeners();

    // Start inactivity check
    startInactivityCheck();

    // Detect tab/browser close
    attachUnloadListener();

    // Tell the server this tab exists, and keep telling it.
    startTabPing();

    console.info('SessionManager initialized. Inactivity timeout(ms):', CONFIG.INACTIVITY_TIMEOUT,
                 'tab ping(ms):', CONFIG.PING_INTERVAL);
  }

  /**
   * Tab/browser close detection.
   *
   * There is no event that reliably means "this tab is closing": beforeunload
   * and pagehide fire on ordinary navigation and on reload too, so a beacon sent
   * from them logs the user out mid-session (and races the next page load).
   *
   * So we invert it. An open tab keeps saying "I'm here" on a timer. Closing the
   * tab, closing the browser, or killing the process all stop the pings by
   * simply not happening, and the server retires the session once the pings are
   * older than TAB_CLOSE_GRACE_SECONDS. Navigation and reload are safe because
   * the next page starts pinging immediately. Several open tabs each ping, so
   * closing one leaves the others working.
   */
  function startTabPing() {
    const ping = function () {
      // A hidden tab is still an open tab, so keep pinging while hidden.
      fetch(CONFIG.PING_ENDPOINT, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin'
      }).catch(function () { /* offline//transient: the next tick retries */ });
    };
    ping();
    setInterval(ping, CONFIG.PING_INTERVAL);
  }

  /**
   * Generate unique tab ID
   */
  function generateTabId() {
    let id = localStorage.getItem(CONFIG.STORAGE_KEY_TAB);
    if (!id || !isValidTabId(id)) {
      id = 'tab_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
      localStorage.setItem(CONFIG.STORAGE_KEY_TAB, id);
    }
    return id;
  }

  /**
   * Check if tab ID is still valid (not too old)
   */
  function isValidTabId(id) {
    try {
      // Extract timestamp from tab ID
      const match = id.match(/^tab_(\d+)_/);
      if (!match) return false;
      const timestamp = parseInt(match[1], 10);
      const age = Date.now() - timestamp;
      // Tab ID is valid if less than 24 hours old
      return age < 24 * 60 * 60 * 1000;
    } catch (e) {
      return false;
    }
  }

  /**
   * Update activity time in memory and localStorage
   */
  function updateActivityTime() {
    lastActivityTime = Date.now();
    resetWarningFlag();
    try {
      localStorage.setItem(CONFIG.STORAGE_KEY, JSON.stringify({
        timestamp: lastActivityTime,
        tabId: tabId
      }));
    } catch (e) {
      console.warn('Failed to update activity time in localStorage:', e);
    }
  }

  /**
   * Attach activity listeners (mouse, keyboard, touch, etc.)
   */
  function attachActivityListeners() {
    const events = ['mousedown', 'keydown', 'touchstart', 'scroll', 'click'];
    const handler = throttle(updateActivityTime, 5000); // Throttle to every 5 seconds

    events.forEach(event => {
      try {
        document.addEventListener(event, handler, { passive: true });
      } catch (e) {
        console.warn('Failed to attach ' + event + ' listener:', e);
      }
    });
  }

  /**
   * Throttle function to limit how often a function can be called
   */
  function throttle(func, delay) {
    let lastCall = 0;
    return function(...args) {
      const now = Date.now();
      if (now - lastCall >= delay) {
        lastCall = now;
        return func.apply(this, args);
      }
    };
  }

  /**
   * Start periodic check for inactivity
   */
  function startInactivityCheck() {
    if (activityCheckInterval) {
      clearInterval(activityCheckInterval);
    }

    activityCheckInterval = setInterval(function() {
      const inactiveTime = Date.now() - lastActivityTime;
      const minutesInactive = Math.floor(inactiveTime / 1000 / 60);

      // Show warning 2 minutes before logout
      if (inactiveTime > CONFIG.INACTIVITY_TIMEOUT - CONFIG.LOGOUT_WARNING_TIME && 
          !hasShownWarning) {
        showInactivityWarning();
        hasShownWarning = true;
      }

      // Force logout if inactive too long
      if (inactiveTime > CONFIG.INACTIVITY_TIMEOUT) {
        console.warn('User inactive for ' + minutesInactive + ' minutes. Forcing logout.');
        performLogout('inactivity');
      }
    }, CONFIG.CHECK_INTERVAL);
  }

  /**
   * Show warning before logout
   */
  function showInactivityWarning() {
    // Create warning banner
    const warning = document.createElement('div');
    warning.id = 'fbp-inactivity-warning';
    warning.className = 'fixed top-4 right-4 p-4 bg-yellow-100 border border-yellow-400 rounded shadow-lg z-50 max-w-sm';
    warning.innerHTML = `
      <div class="flex items-start">
        <div class="flex-shrink-0">
          <svg class="h-5 w-5 text-yellow-600" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
          </svg>
        </div>
        <div class="ml-3">
          <p class="text-sm font-medium text-yellow-800">
            Θα αποσυνδεθείτε λόγω αδράνειας σε περίπου 30 δευτερόλεπτα.
          </p>
          <p class="text-xs text-yellow-700 mt-1">
            Κάντε κάποια ενέργεια για να παραμείνετε συνδεδεμένος.
          </p>
        </div>
      </div>
    `;

    document.body.appendChild(warning);

    // Auto-remove warning after 5 seconds
    setTimeout(() => {
      warning.style.opacity = '0';
      warning.style.transition = 'opacity 0.3s';
      setTimeout(() => warning.remove(), 300);
    }, 5000);
  }

  /**
   * Attach unload listener for tab/browser close
   */
  function attachUnloadListener() {
    // NOTE: there used to be a 'beforeunload' handler here that fired a
    // sendBeacon logout with reason 'tab_close'. It has been removed.
    //
    // beforeunload does not mean "the tab is closing" -- it fires on every full
    // page navigation and on reload too. The beacon was pointed at a URL that
    // did not exist ('/auth/api/logout' -> 404), so it never actually logged
    // anyone out and the breakage stayed invisible. Once the URL was corrected
    // the real behaviour showed up: every login redirect, every form POST
    // redirect and every refresh killed the session server-side and bounced the
    // user straight back to the login page.
    //
    // There is no reliable way to tell a close from a navigation here, and we do
    // not need one: abandoned sessions already expire server-side via
    // last_active_at + SESSION_TIMEOUT_SECONDS in enforce_active_session_claim.
    // The inactivity logout below still works and is the supported path.

    // Detect tab visibility change (when user switches tabs)
    document.addEventListener('visibilitychange', function() {
      if (document.visibilityState === 'hidden') {
        // Tab is hidden - don't reset activity timer
        console.info('Tab hidden');
      } else {
        // Tab is visible - optionally reset inactivity counter
        console.info('Tab visible');
      }
    });
  }

  /**
   * Perform logout
   */
  async function performLogout(reason = 'manual') {
    if (isLoggingOut) return; // Prevent double logout
    isLoggingOut = true;

    console.info('Performing logout. Reason: ' + reason);
      const redirectTo = reason === 'inactivity'
        ? '/login?session_expired=true'
        : '/login';

    // Absolute fallback: never stay logged in if logout request hangs/fails.
    const forceRedirectTimer = setTimeout(() => {
      try { window.location.href = redirectTo; } catch(_) {}
    }, 2500);

    try {
      const response = await fetch(CONFIG.LOGOUT_ENDPOINT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify({ reason: reason }),
        credentials: 'same-origin'
      });

      if (response.ok) {
        clearTimeout(forceRedirectTimer);
        window.location.replace(redirectTo + '&refresh=' + Date.now());
      } else {
        console.error('Logout request failed:', response.status);
        clearTimeout(forceRedirectTimer);
        window.location.replace(redirectTo + '&refresh=' + Date.now());
      }
    } catch (e) {
      console.error('Failed to logout:', e);
      clearTimeout(forceRedirectTimer);
      window.location.replace(redirectTo + '&refresh=' + Date.now());
    }
  }

  /**
   * Reset warning flag when activity occurs
   */
  function resetWarningFlag() {
    hasShownWarning = false;
  }

  /**
   * Expose public API
   */
  window.SessionManager = {
    updateActivity: updateActivityTime,
    resetWarning: resetWarningFlag,
    forceLogout: performLogout,
    getInactiveMinutes: function() {
      return Math.floor((Date.now() - lastActivityTime) / 1000 / 60);
    },
    isLoggingOut: function() {
      return isLoggingOut;
    }
  };

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
