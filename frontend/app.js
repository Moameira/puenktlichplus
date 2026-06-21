const API_BASE = window.PUENKTLICHPLUS_API_BASE || "http://localhost:8000";

const state = {
  routes: [],
};

const legsEl = document.querySelector("#legs");
const resultsEl = document.querySelector("#results");
const template = document.querySelector("#leg-template");
const planner = document.querySelector("#planner");
const stationSuggestionsEl = document.querySelector("#station-suggestions");
let stationSearchTimer;

async function loadRoutes() {
  const response = await fetch(`${API_BASE}/routes`);
  state.routes = await response.json();
}

function uniqueStations() {
  return [...new Set(state.routes.flatMap((route) => [route.origin, route.destination]))].sort();
}

function addLeg(route = state.routes[0], times = {}) {
  const node = template.content.firstElementChild.cloneNode(true);
  const origin = node.querySelector('[name="origin"]');
  const destination = node.querySelector('[name="destination"]');
  origin.value = route?.origin || "";
  destination.value = route?.destination || "";
  node.querySelector('[name="line"]').value = route.line;
  node.querySelector('[name="scheduled_departure"]').value = times.departure || "";
  node.querySelector('[name="scheduled_arrival"]').value = times.arrival || "";
  node.querySelector(".find-trains").addEventListener("click", () => findTrainsForLeg(node));
  for (const input of [origin, destination]) {
    input.addEventListener("input", () => queueStationSuggestions(input.value));
  }
  node.querySelector(".remove").addEventListener("click", () => {
    if (legsEl.children.length > 1) node.remove();
  });
  legsEl.append(node);
  renumberLegs();
}

function renumberLegs() {
  [...legsEl.querySelectorAll(".leg legend")].forEach((legend, index) => {
    legend.textContent = `Abschnitt ${index + 1}`;
  });
}

function readPayload() {
  const legs = [...legsEl.querySelectorAll(".leg")].map((leg) => ({
    origin: leg.querySelector('[name="origin"]').value.trim(),
    destination: leg.querySelector('[name="destination"]').value.trim(),
    line: leg.querySelector('[name="line"]').value.trim() || null,
    scheduled_departure: new Date(leg.querySelector('[name="scheduled_departure"]').value).toISOString(),
    scheduled_arrival: new Date(leg.querySelector('[name="scheduled_arrival"]').value).toISOString(),
  }));
  return { legs };
}

async function predict(event) {
  event.preventDefault();
  resultsEl.innerHTML = `<div class="empty-state"><p class="eyebrow">Rechnet</p><h2>Historische Muster werden sortiert.</h2></div>`;
  try {
    const response = await fetch(`${API_BASE}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readPayload()),
    });
    if (!response.ok) throw new Error(`API antwortet mit Status ${response.status}`);
    renderResults(await response.json());
  } catch (error) {
    resultsEl.innerHTML = `<div class="empty-state"><p class="eyebrow">API nicht erreichbar</p><h2>Bitte Backend starten oder API-Adresse prüfen.</h2><p>${error.message}</p></div>`;
  }
}

function formatTime(value) {
  return new Intl.DateTimeFormat("de-DE", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  }).format(new Date(value));
}

function renderResults(data) {
  const predictions = data.predictions.map((prediction) => {
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
            <strong>${prediction.confidence.replace(" / ", " · ")}</strong>
          </div>
        </div>
        <div class="arrival-strip" aria-label="Ankunftsfenster">
          <div>
            <span>früh</span>
            <strong>${formatTime(window.earliest)}</strong>
          </div>
          <div>
            <span>wahrscheinlich</span>
            <strong>${formatTime(window.likely)}</strong>
          </div>
          <div>
            <span>spät</span>
            <strong>${formatTime(window.latest)}</strong>
          </div>
          <div>
            <span>pessimistisch</span>
            <strong>${formatTime(window.pessimistic)}</strong>
          </div>
        </div>
        <p>${prediction.explanation}</p>
        <p class="fineprint">Datengruppe: ${prediction.sample_size} Beobachtungen</p>
      </article>
    `;
  }).join("");

  const risks = data.transfers.length
    ? data.transfers.map((risk) => `
      <article class="risk ${risk.risk_level}">
        <h3>Umstieg in ${risk.station}</h3>
        <p>${risk.message_de}</p>
        <p class="fineprint">Geplanter Puffer: ${risk.planned_buffer_minutes} Minuten · Risiko: ${Math.round(risk.miss_probability * 100)} Prozent</p>
      </article>
    `).join("")
    : `<p class="fineprint">Keine Umstiege in dieser Suche.</p>`;

  resultsEl.innerHTML = `
    <div class="notice">
      <p class="eyebrow">${data.mode}</p>
      <p>${data.data_notice_de}</p>
    </div>
    <h2>Ankunftsfenster</h2>
    <div class="prediction-list">${predictions}</div>
    <h2>Umstiegsrisiko</h2>
    <div class="risk-list">${risks}</div>
  `;
}

function queueStationSuggestions(query) {
  clearTimeout(stationSearchTimer);
  if (query.length < 2) {
    return;
  }
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
    // Suggestions are helpful, but route entry must still work when DB is unavailable.
  }
}

async function findTrainsForLeg(leg) {
  const origin = leg.querySelector('[name="origin"]').value.trim();
  const destination = leg.querySelector('[name="destination"]').value.trim();
  const optionsEl = leg.querySelector(".train-options");
  if (origin.length < 2 || destination.length < 2) {
    optionsEl.textContent = "Bitte Start und Ziel eingeben.";
    return;
  }

  optionsEl.innerHTML = "DB sucht die nächsten passenden Abfahrten...";
  try {
    const response = await fetch(
      `${API_BASE}/db/next-trains?origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(destination)}`,
    );
    if (!response.ok) {
      const problem = await response.json().catch(() => ({ detail: `Status ${response.status}` }));
      throw new Error(problem.detail || `Status ${response.status}`);
    }
    const data = await response.json();
    if (!data.departures.length) {
      optionsEl.textContent = "Keine direkte Abfahrt im aktuellen Zeitfenster gefunden.";
      return;
    }
    optionsEl.innerHTML = data.departures.map((departure, index) => `
      <button class="train-option" type="button" data-index="${index}">
        <strong>${formatTime(departure.scheduled_departure)} · ${departure.line}</strong>
        <span>${departure.origin} → ${departure.destination} · Gleis ${departure.platform || "offen"}</span>
      </button>
    `).join("");
    [...optionsEl.querySelectorAll(".train-option")].forEach((button) => {
      button.addEventListener("click", () => applyDepartureToLeg(leg, data.departures[Number(button.dataset.index)]));
    });
  } catch (error) {
    optionsEl.innerHTML = `
      <div class="station-error">
        <strong>DB-Abfrage fehlgeschlagen.</strong>
        <span>${error.message}</span>
      </div>
    `;
  }
}

function applyDepartureToLeg(leg, departure) {
  const scheduledDeparture = new Date(departure.scheduled_departure);
  const estimatedArrival = new Date(scheduledDeparture.getTime() + estimateDurationMinutes(departure) * 60_000);
  leg.querySelector('[name="origin"]').value = departure.origin;
  leg.querySelector('[name="destination"]').value = departure.destination;
  leg.querySelector('[name="line"]').value = departure.line;
  leg.querySelector('[name="scheduled_departure"]').value = toInputDateTime(scheduledDeparture);
  leg.querySelector('[name="scheduled_arrival"]').value = toInputDateTime(estimatedArrival);
  leg.querySelector(".train-options").innerHTML = `
    <div class="selected-train">
      <strong>${departure.line} ausgewählt</strong>
      <span>Abfahrt aus DB Timetables. Ankunft ist eine lokale Schätzung für die Prognose.</span>
    </div>
  `;
}

function estimateDurationMinutes(departure) {
  const known = state.routes.find((route) => (
    normalizeStation(route.origin) === normalizeStation(departure.origin)
    && normalizeStation(route.destination) === normalizeStation(departure.destination)
    && route.line === departure.line
  ));
  if (known) return known.typical_minutes;
  const pathStops = Math.max(2, departure.path?.length || 2);
  return Math.min(180, Math.max(20, Math.round((pathStops - 1) * 3.5)));
}

function toInputDateTime(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function normalizeStation(value) {
  return value
    .toLowerCase()
    .replaceAll("ö", "oe")
    .replaceAll("ü", "ue")
    .replaceAll("ä", "ae")
    .replaceAll("ß", "ss");
}

function loadDemo() {
  legsEl.innerHTML = "";
  addLeg(
    { origin: "Köln Hbf", destination: "Düsseldorf Hbf", line: "RE1" },
    { departure: "2026-06-22T07:30", arrival: "2026-06-22T08:05" },
  );
  addLeg(
    { origin: "Düsseldorf Hbf", destination: "Duisburg Hbf", line: "RE1" },
    { departure: "2026-06-22T08:14", arrival: "2026-06-22T08:32" },
  );
}

document.querySelector("#add-leg").addEventListener("click", () => addLeg());
document.querySelector("#load-demo").addEventListener("click", loadDemo);
planner.addEventListener("submit", predict);

await loadRoutes();
loadDemo();
