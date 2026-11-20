# London Companion

**The London know-how your Pippa assistant has been missing.** Ask it about the Tube, a journey, fares, the weather or getting help. London Companion answers in seconds, from live data.

> *"Is the lift working at Morden?"* → **No: step-free access is out for maintenance. Call TfL on 0343 222 1234 to plan a step-free route.**

London Companion is a Pippa Information App. It is stateless and private: it never sees who is asking, and it stores nothing. Every answer comes from official open data, and the app says where each answer came from.

---

## What it can do

### 🚇 Live transport: "Is the Central line running?"
- **Line status** for the Tube, Overground, Elizabeth line, DLR and National Rail, with the reason for any delay
- **Next departures** from a station or bus stop
- **Santander Cycles:** bikes, e-bikes and free docks near you
- **Step-free check:** is the station step-free right now, and are its lifts working? TfL reports about 20 lift faults across London on a typical day. Other apps rarely tell you before you travel.

### 🗺️ Journey planning: "When should I leave to get to Bank by 09:00?"
- **Best route** by your preference: fastest, cheapest, fewest changes, least walking or **step-free**
- **Leave-by time:** works backwards from when you need to arrive, with a safety buffer
- **Late-night journeys:** Night Tube and night buses for getting home after midnight
- Every journey **flags delays and broken lifts on your route**

### 💷 Transportation fares: "What's the cheapest way to pay for the Tube for 3 months?"
- **Fare advisor:** compares contactless and Oyster daily and weekly caps, Travelcards and the **18+ Student Oyster** for your stay, zones and rides per day, and tells you the cheapest option and how much you save
- **Single fares** between two stations, peak and off-peak
- **Airport fares:** Heathrow, Gatwick, Stansted, Luton and City, cheapest first
- Built on 2026 TfL and GLA fare tables. Estimates are labelled as estimates.

### 🌤️ Outdoors: "Is it a good time for a walk?"
- A **go, wait or skip** verdict, how long it will stay dry, air quality, and **minutes until sunset**
- Suggests the **nearest park or walk** from a curated list of London favourites
- Plain-English alerts: rain coming, cold, strong wind, poor air

### 🆘 Safety: "I feel unsafe, where can I get help?"
- **Discreet help options:** 999, the **silent 999 call (press 55)**, British Transport Police **text 61016**, and **Ask for Angela**
- Your **local Met Police neighbourhood team**
- The **nearest station's help point** and whether its lifts work
- **Recorded crime counts** for an area, always with the month the data is from. It never calls a place "safe" or "unsafe", and never calls anyone on your behalf.

---

## Try it in Pippa
Start a message with **@London Companion**, or just ask Pippa naturally:

| You ask | You get |
|---|---|
| *is the tube running* | Every line that isn't running a good service, and why |
| *is the lift working at Morden* | Step-free now: **no**, a lift is out for maintenance, plus TfL's step-free helpline |
| *cheapest way to pay for the tube, I'm a student here for a year* | **18+ Student Oyster + monthly Travelcard**, with the saving against pay as you go |
| *cheapest way from Heathrow to central London* | The cheapest option first (the **Piccadilly line**), compared with the Elizabeth line and Heathrow Express |
| *are there santander bikes near Soho* | The nearest docks, with bikes, e-bikes and free spaces |
| *night bus from Soho to Brixton* | A late route with the fewest walks, plus any delays |
| *is it a good time for a walk* | Go, wait or skip, how long it stays dry, and sunset |
| *who is my local police team near Shepherd's Bush* | Your neighbourhood team, plus discreet ways to get help |

---

## How it's built
A layered set of small, single-purpose modules, all Python standard library:

| Level | What it does | Modules |
|---|---|---|
| **1: Live facts** | Each module fetches one official source | `l1_tfl.py`, `l1_weather.py`, `l1_geo.py`, `l1_police.py`, `l1_fares_data.py` |
| **2: Advice** | Combines level 1 facts into a recommendation | `l2_fare_advisor.py`, `l2_step_free.py`, `l2_best_route.py`, `l2_walk_now.py`, `l2_get_help.py`, `l2_crime_watch.py`, `l2_live.py` |
| **3: Planning** | Combines level 1 and 2 into a whole plan | `l3_leave_by.py`, `l3_night_journey.py` |

`app.py` only routes each question to the right module. Slow lookups run in parallel, so every answer arrives well within Pippa's 20-second limit.

**Data:** Powered by TfL Open Data · Open-Meteo (CC BY 4.0) · postcodes.io · data.police.uk (OGL v3) · 2026 fares from GLA and TfL.

**Coming next:** an evening planner ("dinner at 6 and a sunset on the way"), a going-out check (umbrella and warm layers), landmark lookups such as "Marble Arch", natural times such as "by 6", and shorter answers.

---

*Built for the Pippa Labs hackathon, 20 Nov 2026. The original builder's guide for this template is in `docs/builder-guide.html`.*
