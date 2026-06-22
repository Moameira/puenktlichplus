window.PUENKTLICHPLUS_API_BASE =
  window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost"
    ? "http://localhost:8000"
    : "https://YOUR-RAILWAY-BACKEND.up.railway.app";
