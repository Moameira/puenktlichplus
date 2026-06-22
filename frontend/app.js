const API_BASE = window.PUENKTLICHPLUS_API_BASE || "http://localhost:8000";

const state = {
  offsetMinutes: 0,
  connections: [],
};

const planner = document.querySelector("#planner");
const resultsEl = document.querySelector("#results");
const stationSuggestionsEl = document.querySelector("#station-suggestions");
const connectionResultsEl = document.querySelector("#connection-results");
const apiStatusEl = document.querySelector("#api-status");
const originInput = document.querySelector("#search-origin");
const destinationInput = document.querySelector("#search-destination");
let stationSearchTimer;

async function loadApiStatus() {
  try {
    const response = await fetch(`${API_BASE}/health`);
    if (!response.ok) throw new Error(`Status ${response.status}`);
    const data = await response.json();
    const snapshots = data.collector?.snapshot_count || 0;
    apiStatusEl.innerHTML = `
      <strong>Backend verbunden</strong>
      <span>DB-Zugang: ${data.db_credentials_configured ? "aktiv" : "fehlt"} · Collector-Snapshots: ${snapshots}</span>
    `;
    apiStatusEl.classList.add("ok");
  } catch {
    apiStatusEl.innerHTML = `
      <strong>Backend nicht erreichbar</strong>
      <span>Prüfe die API-Adresse in config.js oder starte FastAPI lokal.</span>
    `;
    apiStatusEl.classList.remove("ok");
  }
}

async function searchConnections(event) {
  event?.preventDefault();
  const origin = originInput.value.trim();
  const destination = destinationInput.value.trim();
  if (origin.length < 2 || destination.length < 2) {
    connectionResultsEl.textContent = "Bitte Start und Ziel eingeben.";
    return;
  }

  connectionResultsEl.innerHTML = "DB sucht passende Verbindungen...";
  try {
    const url = new URL(`${API_BASE}/db/connections`);
    url.searchParams.set("origin", origin);
    url.searchParams.set("destination", destination);
    url.searchParams.set("offset_minutes", String(state.offsetMinutes));
    const response = await fetch(url);
    if (!response.ok) {
      const problem = await response.json().catch(() => ({ detail: `Status ${response.status}` }));
      throw new Error(problem.detail || `Status ${response.status}`);
    }
    const data = await response.json();
    state.connections = data.connections;
    renderConnectionResults(data);
  } catch (error) {
    connectionResultsEl.innerHTML = `
      <div class="station-error">
        <strong>Verbindungssuche fehlgeschlagen.</strong>
        <span>${error.message}</span>
      </div>
    `;
  }
}

function renderConnectionResults(data) {
  if (!data.connections.length) {
    connectionResultsEl.innerHTML = `
      <div class="empty-mini">
        Keine passende Direkt- oder Ein-Umstieg-Verbindung im aktuellen Suchfenster gefunden.
      </div>
    `;
    return;
  }

  const searchedFrom = formatTime(data.searched_from);
  connectionResultsEl.innerHTML = `
    <div class="connection-window">Suche ab ${searchedFrom}</div>
    ${data.connections.map((connection, index) => connectionCard(connection, index)).join("")}
  `;
  [...connectionResultsEl.querySelectorAll(".connection-card")].forEach((button) => {
    button.addEventListener("click", () => selectConnection(Number(button.dataset.index)));
  });
}

function connectionCard(connection, index) {
  const legs = connection.legs.map((leg) => `
    <span>${formatTime(leg.scheduled_departure)} ${leg.line}: ${leg.origin} → ${leg.destination}</span>
  `).join("");
  const kind = connection.transfer_count === 0 ? "Direkt" : `${connection.transfer_count} Umstieg`;
  return `
    <button class="connection-card" type="button" data-index="${index}">
      <strong>${formatClock(connection.departure)} → ${formatClock(connection.arrival)} · ${kind}</strong>
      <span>${connection.duration_minutes} Minuten · ${connection.legs.map((leg) => leg.line).join(" + ")}</span>
      <div class="connection-legs">${legs}</div>
    </button>
  `;
}

async function selectConnection(index) {
  const connection = state.connections[index];
  connectionResultsEl.querySelectorAll(".connection-card").forEach((card) => {
    card.classList.toggle("selected", Number(card.dataset.index) === index);
  });

  resultsEl.innerHTML = `<div class="empty-state"><p class="eyebrow">Prüft</p><h2>Die ausgewählte Verbindung wird bewertet.</h2></div>`;
  try {
    const response = await fetch(`${API_BASE}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ legs: connection.legs.map(toPredictionLeg) }),
    });
    if (!response.ok) throw new Error(`API antwortet mit Status ${response.status}`);
    renderResults(await response.json(), connection);
  } catch (error) {
    resultsEl.innerHTML = `<div class="empty-state"><p class="eyebrow">Prüfung fehlgeschlagen</p><h2>${error.message}</h2></div>`;
  }
}

function toPredictionLeg(leg) {
  return {
    origin: leg.origin,
    destination: leg.destination,
    line: leg.line,
    scheduled_departure: leg.scheduled_departure,
    scheduled_arrival: leg.scheduled_arrival,
  };
}

function renderResults(data, connection) {
  const summary = summarizeRisk(data.transfers, connection);
  const risks = data.transfers.length
    ? data.transfers.map((risk) => `
      <article class="risk ${risk.risk_level}">
        <h3>Umstieg in ${risk.station}</h3>
        <p>${risk.message_de}</p>
        <p class="fineprint">Geplanter Puffer: ${risk.planned_buffer_minutes} Minuten · Risiko: ${Math.round(risk.miss_probability * 100)} Prozent</p>
      </article>
    `).join("")
    : `<p class="fineprint">Diese Verbindung ist direkt. Es gibt keinen Umstieg zu verpassen.</p>`;

  const predictions = data.predictions.map((prediction) => predictionCard(prediction)).join("");

  resultsEl.innerHTML = `
    <section class="risk-summary ${summary.level}">
      <p class="eyebrow">Ergebnis</p>
      <h2>${summary.title}</h2>
      <p>${summary.body}</p>
    </section>
    <div class="notice">
      <p class="eyebrow">${data.mode}</p>
      <p>${data.data_notice_de}</p>
    </div>
    <h2>Gewählte Verbindung</h2>
    <div class="selected-route">${connection.legs.map((leg) => `
      <div>
        <strong>${formatClock(leg.scheduled_departure)} · ${leg.line}</strong>
        <span>${leg.origin} → ${leg.destination}</span>
      </div>
    `).join("")}</div>
    <h2>Umstiegsrisiko</h2>
    <div class="risk-list">${risks}</div>
    <h2>Warum?</h2>
    <div class="prediction-list">${predictions}</div>
  `;
}

function predictionCard(prediction) {
  const window = prediction.arrival_window;
  return `
    <article class="prediction">
      <div class="prediction-head">
        <div>
          <p class="eyebrow">${prediction.line}</p>
          <h3>${prediction.origin} → ${prediction.destination}</h3>
        </div>
        <div class="delay-badge">+${prediction.expected_delay_minutes} min</div>
      </div>
      <div class="metric-grid">
        <div class="metric">
          <span>Planankunft</span>
          <strong>${formatTime(prediction.scheduled_arrival)}</strong>
        </div>
        <div class="metric primary">
          <span>Realistisch</span>
          <strong>${formatTime(window.likely)}</strong>
        </div>
        <div class="metric">
          <span>Verlässlichkeit</span>
          <strong>${prediction.confidence}</strong>
        </div>
      </div>
      <div class="arrival-strip" aria-label="Ankunftsfenster">
        <div><span>früh</span><strong>${formatTime(window.earliest)}</strong></div>
        <div><span>wahrscheinlich</span><strong>${formatTime(window.likely)}</strong></div>
        <div><span>spät</span><strong>${formatTime(window.latest)}</strong></div>
        <div><span>pessimistisch</span><strong>${formatTime(window.pessimistic)}</strong></div>
      </div>
      <p>${prediction.explanation}</p>
      <p class="fineprint">Datengruppe: ${prediction.sample_size} Beobachtungen</p>
    </article>
  `;
}

function summarizeRisk(transfers, connection) {
  if (!transfers.length) {
    return {
      level: "low",
      title: "Direktverbindung: kein Umstiegsrisiko",
      body: `Diese Verbindung hat ${connection.transfer_count} Umstiege. Bewertet wird trotzdem die realistische Ankunftszeit.`,
    };
  }

  const worst = [...transfers].sort((a, b) => scoreRisk(b) - scoreRisk(a))[0];
  const probability = Math.round(worst.miss_probability * 100);
  if (worst.risk_level === "invalid") {
    return {
      level: "invalid",
      title: "Dieser Anschluss funktioniert im Plan schon nicht",
      body: `Der nächste Zug in ${worst.station} fährt vor der geplanten Ankunft ab.`,
    };
  }
  if (worst.risk_level === "high") {
    return {
      level: "high",
      title: "Riskanter Umstieg",
      body: `Der kritischste Umstieg ist in ${worst.station}: ${worst.planned_buffer_minutes} Minuten Puffer, ungefähr ${probability} Prozent Verpass-Risiko.`,
    };
  }
  if (worst.risk_level === "medium") {
    return {
      level: "medium",
      title: "Knapp, aber vertretbar",
      body: `Der engste Umstieg ist in ${worst.station}: ${worst.planned_buffer_minutes} Minuten Puffer, ungefähr ${probability} Prozent Risiko.`,
    };
  }
  return {
    level: "low",
    title: "Sieht solide aus",
    body: `Der kritischste Umstieg ist in ${worst.station}, liegt aber nur bei ungefähr ${probability} Prozent Verpass-Risiko.`,
  };
}

function scoreRisk(risk) {
  return { invalid: 4, high: 3, medium: 2, low: 1 }[risk.risk_level] || 0;
}

function queueStationSuggestions(query) {
  clearTimeout(stationSearchTimer);
  if (query.length < 2) return;
  stationSearchTimer = setTimeout(() => loadStationSuggestions(query), 260);
}

async function loadStationSuggestions(query) {
  try {
    const response = await fetch(`${API_BASE}/db/stations?pattern=${encodeURIComponent(query)}`);
    if (!response.ok) return;
    const data = await response.json();
    stationSuggestionsEl.innerHTML = data.stations
      .map((station) => `<option value="${station.name}"></option>`)
      .join("");
  } catch {
    // Suggestions are optional; manual typing should still work.
  }
}

function shiftSearch(minutes) {
  state.offsetMinutes += minutes;
  searchConnections();
}

function formatTime(value) {
  return new Intl.DateTimeFormat("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  }).format(new Date(value));
}

function formatClock(value) {
  return new Intl.DateTimeFormat("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

for (const input of [originInput, destinationInput]) {
  input.addEventListener("input", () => queueStationSuggestions(input.value));
}

planner.addEventListener("submit", searchConnections);
document.querySelector("#previous-connections").addEventListener("click", () => shiftSearch(-60));
document.querySelector("#later-connections").addEventListener("click", () => shiftSearch(60));

await loadApiStatus();
