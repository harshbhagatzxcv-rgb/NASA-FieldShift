<div align="center">

# 🌍 FieldShift

### Climate-Resilient Crop Rotation Planning with NASA Earth Observations

**NASA Space Apps Challenge 2026 | Field Shift: Adapting Farms with NASA Data**

An explainable agricultural decision-support platform combining NASA Earth observations, soil information, and farmer priorities to explore climate-resilient crop rotation strategies.

**[GitHub Repository](https://github.com/harshbhagatzxcv-rgb/NASA-FieldShift)**

</div>

---

## 🌱 Overview

Farmers worldwide face increasingly unpredictable rainfall, rising temperatures, water shortages, extreme weather, and declining soil health.

**FieldShift** helps farmers explore alternative crop rotation strategies using regional environmental observations, soil-property estimates, and transparent agricultural decision rules.

The platform aims to support:

- Climate-resilient crop planning
- Soil health improvement
- Water conservation
- Crop diversification
- Agricultural decision-making under uncertainty

FieldShift is designed to support farmer judgment, not replace local agricultural expertise.

---

## ✨ Key Features

### 🌍 Location-Based Farm Analysis

Farmers can select predefined demonstration farms or enter custom geographic coordinates.

The application uses latitude and longitude to request regional environmental information.

Custom-location analysis depends on the availability of external environmental datasets.

### 🛰️ NASA Climate Integration

FieldShift integrates NASA POWER climate observations.

Requested environmental variables include:

- Maximum temperature (`T2M_MAX`)
- Minimum temperature (`T2M_MIN`)
- Precipitation (`PRECTOTCORR`)
- Relative humidity (`RH2M`)
- Solar radiation (`ALLSKY_SFC_SW_DWN`)

The backend derives climate indicators including:

- Seasonal rainfall estimates
- Rainfall anomalies
- Growing degree days
- Heat-stress days

### 🌾 Crop Rotation Recommendations

The application evaluates three-year crop rotation strategies using a deterministic, explainable recommendation engine.

Factors considered include:

- Climate suitability
- Water requirements
- Soil compatibility
- Crop-family diversity
- Root characteristics
- Farmer priorities

Recommendations include comparative scores, explanations, and warnings.

**Important:** Scores are comparative planning indicators, not predictions of agricultural yield.

### 💧 Climate Scenario Simulation

Farmers can explore hypothetical environmental changes by adjusting:

- Rainfall
- Temperature
- Irrigation availability

The application recalculates crop rotation suitability under the selected scenario.

### 📊 Transparent Data Provenance

FieldShift distinguishes environmental information using the following statuses:

| Status | Meaning |
|---|---|
| LIVE | Successfully retrieved and validated external data |
| CACHED | Previously retrieved data reused |
| ESTIMATED | Model-derived or estimated information |
| FALLBACK | Substitute information used when primary data is unavailable |
| UNAVAILABLE | Reliable information could not be retrieved |

These labels help users distinguish real observations from estimates and demonstration values.

---

## 🛠️ Technology Stack

| Component | Technology |
|---|---|
| Frontend | React, Next.js 16 |
| Programming Language | TypeScript |
| Backend | Python, FastAPI |
| Climate Data | NASA POWER |
| Soil Data | ISRIC SoilGrids |
| Soil Moisture | NASA SMAP — integration pending |
| API Communication | REST / JSON |
| Version Control | Git and GitHub |

---

## 📁 Project Structure

```text
NASA-FieldShift/
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   ├── public/
│   ├── package.json
│   └── package-lock.json
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── data/
│   │   ├── models/
│   │   ├── services/
│   │   └── main.py
│   ├── tests/
│   └── requirements.txt
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## ⚙️ Getting Started

### Prerequisites

Install the following:

- Git
- Node.js and npm
- Python 3.10 or later
- VS Code or another code editor

### 1. Clone the Repository

```bash
git clone https://github.com/harshbhagatzxcv-rgb/NASA-FieldShift.git
cd NASA-FieldShift
```

### 2. Set Up the Backend

Open a PowerShell terminal:

```powershell
cd backend
python -m venv .venv
```

Install dependencies:

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

Enable live environmental data requests:

```powershell
$env:DEMO_MODE="false"
```

Start FastAPI:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Backend API:

http://localhost:8000

Interactive API documentation:

http://localhost:8000/docs

### 3. Set Up the Frontend

Open a second PowerShell terminal from the project root:

```powershell
cd frontend
npm ci
```

Configure the backend URL:

```powershell
Set-Content .env.local "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000"
```

Start the development server:

```powershell
npm run dev
```

Open:

http://localhost:3000

Keep both frontend and backend terminals running.

---

## 🔌 Backend API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/health` | Backend health check |
| GET | `/api/v1/crops` | Retrieve crop information |
| GET | `/api/v1/demo-profiles` | Retrieve demonstration farm profiles |
| POST | `/api/v1/farms/evaluate` | Evaluate crop rotation strategies |
| POST | `/api/v1/farms/simulate` | Simulate climate scenarios |

API documentation is available through FastAPI Swagger UI.

---

## 🛰️ Environmental Data Sources

### NASA POWER

**Website:** https://power.larc.nasa.gov/

NASA POWER provides regional climate observations used to calculate temperature, rainfall, and heat-related indicators.

Live retrieval has been demonstrated during development.

### ISRIC SoilGrids

**Website:** https://soilgrids.org/

SoilGrids provides modeled soil-property estimates, including:

- Soil texture
- Soil pH
- Soil organic carbon
- Clay and sand fractions

The REST API has experienced HTTP 503 errors during development.

An alternative Web Coverage Service (WCS) adapter is being developed and validated.

SoilGrids estimates are not substitutes for laboratory soil tests.

### NASA SMAP

**Website:** https://smap.jpl.nasa.gov/

NASA SMAP provides satellite-based soil-moisture information.

The planned integration may require NASA Earthdata authentication.

SMAP observations are not yet fully integrated into FieldShift.

---

## 🧪 Testing

### Backend Tests

From the backend directory:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest
```

### Frontend TypeScript Validation

From the frontend directory:

```powershell
npx tsc --noEmit
```

### Frontend Production Build

```powershell
npm run build
```

These commands help verify the codebase but do not guarantee external API availability.

---

## 🌦️ How FieldShift Works

```text
                  FARMER
                    |
                    v
          Select Farm Location
                    |
                    v
        Enter Agricultural Details
                    |
                    v
          Environmental Analysis
                    |
          +---------+---------+
          |                   |
          v                   v
      NASA POWER          SoilGrids
          |                   |
          +---------+---------+
                    |
                    v
          Crop Suitability Rules
                    |
                    v
          Rotation Optimization
                    |
                    v
          Ranked Crop Strategies
                    |
                    v
          Climate Scenario Testing
                    |
                    v
        Explainable Recommendations
```

The system combines environmental information with configurable crop characteristics and farmer priorities.

It does not claim to predict exact yields or future weather.

---

## ⚠️ Known Limitations

FieldShift is currently under active development.

Known limitations include:

- SoilGrids REST API availability may vary.
- SoilGrids WCS integration requires further validation.
- NASA SMAP integration is incomplete.
- Demonstration profiles may use predefined environmental values.
- Some custom locations may have unavailable environmental information.
- Regional satellite observations may not represent individual field conditions.
- Crop rotation scores are comparative indicators.
- Local soil testing and agricultural expertise remain important.

The application should not present fallback or demonstration information as live observations.

---

## 🤝 Contributing

Contributions are welcome.

Team members should create separate development branches rather than modifying `main` directly.

### Create a Branch

```bash
git switch -c feature/your-feature
```

### Commit Changes

```bash
git add .
git commit -m "Describe your changes"
```

### Push Your Branch

```bash
git push -u origin feature/your-feature
```

Then open a Pull Request on GitHub.

Team members without direct repository write access can fork the project and submit Pull Requests.

---

## 🔐 Security

Never commit:

- API keys
- Access tokens
- Passwords
- NASA Earthdata credentials
- `.env.local`
- Python virtual environments
- `node_modules`
- Next.js build caches
- Private keys

Use environment variables for sensitive configuration.

---

## 🌎 NASA Space Apps Challenge 2026

**Challenge:** Field Shift: Adapting Farms with NASA Data

FieldShift explores how Earth observations, soil information, and explainable decision-support systems can help farmers evaluate crop rotation strategies under changing environmental conditions.

The project emphasizes:

- Environmental resilience
- Soil health
- Water conservation
- Transparent scientific evidence
- Farmer-centered decision-making

---

## 📚 Attribution

This project builds upon the original FieldShift repository:

**[Original FieldShift Repository](https://github.com/tejaswi-raghav/field-shift)**

Original authorship, copyright notices, and applicable licensing conditions must be respected.

NASA and ISRIC datasets remain subject to their respective data-use policies.

---

## 🚧 Project Status

**Active Development**

Current development priorities:

1. Reliable custom-coordinate analysis
2. Validated location-specific soil estimates
3. NASA SMAP integration
4. Improved crop rotation evaluation
5. Automated testing
6. Production deployment

---

<div align="center">

### 🌱 FieldShift

**Exploring climate-resilient farming through Earth observation data.**

Built for NASA Space Apps Challenge 2026.

</div>