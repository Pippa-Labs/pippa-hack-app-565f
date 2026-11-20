# Pocket Londoner

**A local in your pocket for your Pippa assistant.** Ask it about the Tube, a journey, fares, the weather or getting home safely. It answers in one short line, straight from live London data.

> *"Is the lift working at Morden?"* → **Morden is step-free, but a lift is out of service. Call TfL 0343 222 1234 before you travel.**

Pocket Londoner is a Pippa Information App. It is stateless and private: it never sees who is asking, and it stores nothing. Every answer comes from official open data, and the app says where each answer came from.

---

## ⭐ What makes it different

### 🛗 Live lift faults, before you leave home
TfL reports around **20 broken lifts across London on a typical day**, but most map apps never tell you a lift is out. You only find out when you're standing at the bottom of the stairs with a wheelchair, a buggy or a suitcase.

Pocket Londoner checks TfL's live lift-fault feed. Ask *"is the lift working at Morden?"* or *"is King's Cross step-free right now?"* and you get a straight yes or no. If the answer is no, it tells you what to do instead.

Every journey it plans **flags broken lifts on your route**. Ask for a **step-free route** and it plans one, and warns you if a lift on that route is out right now.

### 🌙 Getting home safely at night
Say where you're going back to, and if you'll be travelling after dark, Pocket Londoner plans the trip home:
- a **late route** with the Night Tube and night buses, the least walking, and any delays or broken lifts on the way
- the **stations with help points** along that route
- **discreet ways to get help:** the silent 999 call (dial 999, then press 55), British Transport Police **text 61016**, and **Ask for Angela**
- **recorded crime counts** near the walking parts, always with the month the data is from

It never labels a place "safe" or "unsafe", and never contacts anyone for you. It gives you the facts and the help options, and the choice stays yours.

### ✂️ Answers short enough for anyone
No paragraphs. Every answer leads with **one line of 15 words or fewer**, then up to **three tiny bullet points**. Plain words, simple times, nothing to decode:

> *"Fastest way from South Ken to Shepherd's Bush?"* → **District + H&C, 39 min. £2.80 contactless.**
>
> *"I have £3, what's the fare from South Ken to Shepherd's Bush?"* → **£2.80 off-peak with contactless. Within your £3.**

---

## Everything it can do

| Ask about | Example | What you get |
|---|---|---|
| 🚇 **Live transport** | *is the central line running* | Line status for the Tube, Overground, Elizabeth line, DLR and National Rail, plus next departures, Santander bikes and free docks, and step-free checks |
| 🗺️ **Journeys** | *when should I leave to get to Bank by 9* | A leave-by time, the best route (fastest, cheapest, fewest changes, least walking or step-free), and late-night routes home |
| ☂️ **Going out** | *do I need an umbrella tonight* | Take an umbrella if rain or snow is likely, and "wear more" when it will feel colder than 5°C |
| 🌇 **Evening plans** | *dinner at Marble Arch at 6, can I catch the sunset* | Whether you can make the sunset first, a nearby spot to watch it from, and the weather on the way |
| 💷 **Transportation fares** | *cheapest way to pay for the tube, I'm a student here for a year* | Contactless versus Oyster versus Travelcards versus the 18+ Student Oyster, with your saving, plus single fares and airport fares |
| 🌤️ **Outdoors** | *is it a good time for a walk* | Go, wait or skip, how long it will stay dry, air quality, sunset, and a nearby park |
| 🆘 **Safety** | *I feel unsafe, where can I get help* | Discreet help options (silent 999 with 55, text 61016, Ask for Angela), and your local police neighbourhood team with the 101 non-emergency number |

Pocket Londoner understands landmarks ("Marble Arch"), stations, postcodes and everyday times ("by 6", "half six").

---

## How it's built
A layered set of small, single-purpose modules, all Python standard library:

| Level | What it does | Examples |
|---|---|---|
| **1: Live facts** | Each module fetches one official source | `l1_tfl.py`, `l1_weather.py`, `l1_geo.py`, `l1_police.py`, `l1_fares_data.py` |
| **2: Advice** | Combines level 1 facts into a recommendation | `l2_step_free.py`, `l2_going_out.py`, `l2_fare_advisor.py`, `l2_best_route.py`, `l2_get_help.py`, `l2_crime_watch.py` |
| **3: Planning** | Combines level 1 and 2 into a whole plan | `l3_leave_by.py`, `l3_night_journey.py` |

`app.py` only routes each question to the right module. Slow lookups run in parallel, so every answer arrives well within Pippa's 20-second limit.

**Data:** Powered by TfL Open Data · Open-Meteo (CC BY 4.0) · postcodes.io · OpenStreetMap · data.police.uk (OGL v3) · 2026 fares from GLA and TfL.

---

*Built for the Pippa Labs hackathon, 20 Nov 2026. The original builder's guide for this template is in `docs/builder-guide.html`.*
