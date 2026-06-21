# PünktlichPlus

PünktlichPlus predicts realistic arrival windows for Deutsche Bahn routes in NRW and flags risky transfers. The point is deliberately practical: a recruiter should see data handling, a clean backend architecture, and an honest explanation of what the model can and cannot know.

The UI is German-only for v1 because the target use case is a German railway portfolio demo.

## What It Does

- Builds an itinerary with one or more train legs.
- Lets users type station names and fetch the earliest two matching DB Timetables departures for a route.
- Predicts an arrival window instead of pretending one exact minute is scientific.
- Groups delay behavior by line, hour of day, and weekday.
- Calculates transfer risk from the first leg's delay distribution versus the planned buffer.
- Keeps the data layer, DB cache/rate limit, model, transfer-risk logic, and API separate.

## Data Feasibility Check

DB's public Open Data page says the old portal moved on March 10, 2024 and that DB APIs remain available through the [DB API Marketplace](https://developers.deutschebahn.com/db-api-marketplace/apis/). It specifically describes the [Timetables API](https://developers.deutschebahn.com/db-api-marketplace/apis/product/timetables) as a way to query current timetable slices and current deviations from the planned timetable.

The Timetables product currently lists a free plan with 60 calls per minute and endpoints such as `/station/{pattern}`, `/plan/{evaNo}/{date}/{hour}`, `/fchg/{evaNo}`, and `/rchg/{evaNo}`. The Marketplace getting-started guide says registration, an application, a client id, and an API key are required.

Important limitation: this is not a free historical per-route delay archive. For v1, PünktlichPlus uses DB Timetables for live station lookup and near-term departures, then uses a representative NRW dataset in `backend/app/data/nrw_delay_observations.csv` for historical delay modeling. That separation is intentional and documented in the API response.

The backend protects DB's free quota with local JSON caching and a conservative 20-calls-per-minute app-side rate limit, below DB's listed 60-calls-per-minute free plan.

## Architecture

```text
backend/
  app/main.py                    FastAPI routes
  app/services/data_source.py    CSV repository and DB Timetables adapter
  app/services/delay_model.py    Explainable delay distribution model
  app/services/connection_risk.py Transfer miss probability
  app/services/cache.py          Local JSON cache for external API calls
  app/services/rate_limit.py     App-side DB quota protection
frontend/
  index.html                     Static app shell
  styles.css                     Design tokens and sketch-like UI
  app.js                         Itinerary form and API rendering
```

Python/FastAPI is used because the core work is data-heavy and the modeling code should stay interview-readable. The frontend is static HTML/CSS/JS so it can deploy cheaply to GitHub Pages, Netlify, or Vercel while the backend runs separately on a free-tier Python host.

## Run Locally

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open `frontend/index.html` in a browser. For deployed frontend builds, set:

```html
<script>
  window.PUENKTLICHPLUS_API_BASE = "https://your-api-host.example";
</script>
```

before `frontend/app.js`, or replace the default in `frontend/app.js`.

## Optional DB Credentials

Create a free DB API Marketplace account, create an application, subscribe to the free Timetables plan, then set:

```bash
DB_CLIENT_ID=...
DB_API_KEY=...
```

The current model does not claim those calls as historical training data. They are used for station suggestions and the earliest two live departures for a typed origin-destination pair. DB Timetables does not provide a full journey planner response here, so the selected live train supplies the departure time and line, while the destination arrival time is locally estimated for the prediction workflow.

## Testing

```bash
cd backend
pytest
```

The tests cover the explainable prediction window, station-name normalization, DB XML parsing, and transfer-risk calculation. `backend/app/data/validation_cases.json` contains small validation cases for portfolio discussion.

## Limitations

- v1 is NRW-only.
- The sample dataset is representative, not official DB historical ground truth.
- DB Timetables is used for station boards, not full route planning.
- Delay distributions are grouped statistics, not a black-box ML model.
- The model does not yet account for weather, construction works, rolling stock, cancellations, or platform changes.
- Transfer risk assumes the next train leaves according to its planned departure time.

Those limitations are not hidden because the engineering value here is showing a trustworthy system, not overselling a demo.
