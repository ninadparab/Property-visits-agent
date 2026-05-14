# Open House Visit Planner

An AI agent that finds open houses on Redfin, computes travel times via Google Maps, and builds an optimized visit schedule for a given day. Runs as a Streamlit chat UI or a command-line agent.

## What it does

1. **Scrapes Redfin** for open houses matching your filters (city, beds, price, property type)
2. **Computes travel times** between all properties using Google Maps Distance Matrix API
3. **Builds an optimized schedule** using Google OR-Tools VRPTW (Vehicle Routing with Time Windows) — maximizes the number of houses you can visit within their open house windows
4. **Asks follow-up questions** — if 0 results are found, prompts you to relax filters; after showing the schedule, lets you adjust timing, location, or filters without starting over

## Demo

![Streamlit chat UI](https://your-screenshot-url-here)

## Architecture

```
User input (natural language)
    │
    ▼
parse_node          — LLM extracts city, filters, start time, home address
    │
    ▼
input_node          — validates required fields
    │
    ▼
scraper_node        — calls Redfin JSON API, returns listings with open house times
    │
    ├─ 0 results → filter_update_node  (interrupt: ask user to relax filters)
    │
    ▼
distance_node       — filters to target day, builds travel time matrix via Google Maps
    │
    ▼
scheduler_node      — OR-Tools VRPTW finds optimal visit order
    │
    ├─ unschedulable → replan_node (flags skipped houses)
    │
    ▼
output_node         — formats itinerary
    │
    ▼
feedback_node       — (interrupt: ask user if they want to change anything)
    │
    ├─ filters  → scraper_node
    ├─ time     → scheduler_node
    ├─ location → distance_node
    ├─ restart  → parse_node
    └─ done     → END
```

**Tools**

| File | Purpose |
|---|---|
| `redfin_tool.py` | Scrapes Redfin JSON search API for open houses with confirmed times |
| `google_maps_tool.py` | Builds N×N travel time matrix via Distance Matrix API (auto-batched) |
| `scheduler_tool.py` | OR-Tools VRPTW scheduler — finds max visits within time windows |

**Agent files**

| File | Purpose |
|---|---|
| `state.py` | LangGraph `AgentState` TypedDict |
| `nodes.py` | All graph node functions |
| `graph.py` | Builds the `StateGraph` (also exposes `agent` for LangGraph Studio) |
| `streamlit_app.py` | Streamlit chat UI |
| `main.py` | CLI entry point |
| `workflow.py` | Simple deterministic pipeline (no agent, no interrupts) |

## Setup

### Prerequisites

- Python 3.11+
- [Anthropic API key](https://console.anthropic.com)
- [Google Maps API key](https://console.cloud.google.com) with **Distance Matrix API** enabled

### Install

```bash
git clone https://github.com/your-username/property-visits-agent
cd property-visits-agent

python -m venv .venv
# Windows
.venv\Scripts\activate
# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Configure

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_MAPS_API_KEY=AIza...
```

## Running locally

### Streamlit UI (recommended)

```bash
streamlit run streamlit_app.py
```

Opens at `http://localhost:8501`. Use the sidebar to fill in search parameters or type a natural language query in the chat box.

**Example query:**
> Find open houses in Kirkland, WA this Saturday. I'll leave at 9 AM from 500 108th Ave NE, Bellevue WA. 3+ beds, max $1.5M, houses and townhouses only.

### Command-line agent

```bash
python main.py
```

### LangGraph Studio (graph visualization)

```bash
pip install langgraph-cli
langgraph dev
```

Opens the Studio UI at `http://localhost:8123` for visual graph inspection and debugging.

## Deploying to Streamlit Community Cloud

1. Push to GitHub (`.env` is gitignored — never commit your keys)
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → select your repo
3. Set **Main file path** to `streamlit_app.py`
4. Click **Advanced settings → Secrets** and add:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
GOOGLE_MAPS_API_KEY = "AIza..."
```

5. Click **Deploy**

## City coverage

The `region_map.json` file caches Redfin region IDs for Seattle-area cities. For other cities, the tool automatically looks up the region ID via Redfin's API and validates it before use. If you search a new city and it fails, you can add it manually:

```json
"Portland_OR": "30818"
```

Find the region ID from the Redfin city page URL: `redfin.com/city/{region_id}/OR/Portland`

## Key design decisions

**Why OR-Tools instead of a greedy scheduler?**
A greedy "pick the earliest window first" approach fails when visiting A→B→C is better than A→C→B even though C opens earlier. OR-Tools finds the globally optimal route across all candidates.

**Why Redfin's JSON API instead of CSV?**
Redfin's CSV download (`/stingray/api/gis-csv`) never populates the `NEXT OPEN HOUSE START TIME` column — it's always blank. The JSON API (`/stingray/api/gis`) returns `openHouseStart`/`openHouseEnd` as Unix millisecond timestamps.

**Why filter by day before building the travel matrix?**
The Google Maps Distance Matrix API is billed per element (origin × destination). Building an N×N matrix for all weekend listings and then filtering wastes API quota. Filtering to the user's target day first (e.g., Saturday only) typically cuts the matrix from 35×35 to 15×15.

**Why LangGraph instead of a simple script?**
The `interrupt()` mechanism lets the graph pause mid-execution for human input — asking the user to relax filters when 0 results are found, or collecting schedule feedback — and resume exactly where it left off. This would require significant custom state management in a plain script.

## Dependencies

- [LangGraph](https://github.com/langchain-ai/langgraph) — agent graph with interrupt/resume
- [LangChain Anthropic](https://python.langchain.com/docs/integrations/chat/anthropic) — Claude for NL parsing and feedback classification
- [Google OR-Tools](https://developers.google.com/optimization) — VRPTW solver
- [googlemaps](https://github.com/googlemaps/google-maps-services-python) — Distance Matrix API client
- [Streamlit](https://streamlit.io) — chat UI
- [Redfin](https://www.redfin.com) — open house data (scraped, no API key required)
