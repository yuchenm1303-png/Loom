// Runs before the renderer bundle, from index.html, without being inlined there:
// the page's Content-Security-Policy allows no inline script.

// Paint the saved theme before React mounts so the window never flashes the other one.
(function () {
  var preference = "dark";
  try {
    var stored = JSON.parse(localStorage.getItem("loom.settings.desktop.v2") || "{}");
    var value = stored && stored.appearance && stored.appearance.theme;
    if (value === "light" || value === "dark" || value === "system") preference = value;
  } catch (_) {}
  var dark = preference === "dark" || (preference === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  var resolved = dark ? "dark" : "light";
  document.documentElement.dataset.loomThemePreference = preference;
  document.documentElement.dataset.loomTheme = resolved;
  document.documentElement.style.colorScheme = resolved;
})();

// Until React replaces the boot screen, show script failures on it instead of a blank window.
(function () {
  function showBootFailure(label, value) {
    var detail = document.getElementById("boot-detail");
    if (!detail) return;
    var message = value && value.message ? value.message : String(value || "Unknown renderer error");
    detail.textContent = label + "\n" + message;
    detail.style.color = "#dc7c86";
  }
  window.addEventListener("error", function (event) {
    showBootFailure("Renderer script error", event.error || event.message);
  });
  window.addEventListener("unhandledrejection", function (event) {
    showBootFailure("Renderer promise rejection", event.reason);
  });
})();
