/*
 * Spark fixture site — auth guard.
 *
 * Include this with a plain, synchronous <script src="auth-guard.js"></script>
 * near the top of <head> on any page that should require the fake login.
 *
 * Behaviour:
 *   - If the current URL has ?nologin=1, the guard does nothing. This is a
 *     test-only escape hatch (see README.md) so automated tests can load
 *     pages directly without driving the login form first.
 *   - Otherwise, if the "spark_fixture_session" cookie is not present, the
 *     browser is redirected to login.html?next=<current path + query>.
 *
 * This file intentionally has no dependencies and does not use
 * DOMContentLoaded — it must run and redirect before the rest of the page
 * is parsed.
 */
(function () {
  "use strict";

  function getQueryParam(name) {
    var params = new URLSearchParams(window.location.search);
    return params.get(name);
  }

  function hasSessionCookie() {
    var cookies = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < cookies.length; i++) {
      var pair = cookies[i].trim();
      if (pair.indexOf("spark_fixture_session=") === 0) {
        var value = pair.substring("spark_fixture_session=".length);
        return value === "1";
      }
    }
    return false;
  }

  if (getQueryParam("nologin") === "1") {
    return;
  }

  if (!hasSessionCookie()) {
    var next = window.location.pathname + window.location.search;
    window.location.replace("login.html?next=" + encodeURIComponent(next));
  }
})();
