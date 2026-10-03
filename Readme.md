# Weather Advisor

A chat bot that answers questions like *"Is it safe to cycle to work in Bhopal
this evening?"* using live weather from Open-Meteo. Every piece of advice comes
from a written policy file (`sops/sops.yaml`), never from the model's own
judgement. If no policy covers the question, the bot says so instead of guessing.

```
you> Is it safe to bike to work in Bhopal today?

Bhopal, India · today (Thu 03 Sep 07:00–22:00)

Main concern — Organised heavy rain over the location [GEN-01, danger]
A sustained heavy-rain pattern is forecast for this location. Treat any outdoor
plan as high risk ... check the latest IMD district warning before stepping out.
Why: rain expected over the next 24 hours 110.4 mm — at or above 64.5 mm

Also — Strong gusts on a two-wheeler [TW-01, warning]
...
Based on: GEN-01 (danger), TW-01 (warning) · Open-Meteo forecast, issued 2026-09-03T07:30 local time
```

---

## Setup

Python 3.10 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate             # Windows
source .venv/bin/activate          # Mac / Linux
pip install -r requirements.txt
```

Create a file called `.env` in the project folder with one line:

```
GROQ_API_KEY=your_key_here
```

A free key is available at console.groq.com. The model is Groq's
`openai/gpt-oss-20b` (set in `advisor/llm.py`). Open-Meteo needs no key.

## Run

| What | Command |
|---|---|
| Chat UI (frontend) | `streamlit run app.py` → opens http://localhost:8501 |
| Terminal chat | `python cli.py` (add `--trace` to see the route through the graph) |
| Eval suite | `python evals/run_evals.py` (add `--no-live` to skip the live API case) |
| Regenerate test fixtures | `python evals/make_fixtures.py` |

In the chat UI, every reply has a **"Why did it say that?"** panel showing how
the question was understood, the path it took through the graph, and the exact
forecast numbers the rules were checked against. The sidebar lists the active
SOPs and has a "New session" button.

---

## Project layout

```
sops/sops.yaml          all policies, plus the activity and group vocabulary
advisor/
  metrics.py            the 12 weather numbers a rule can test
  sop_store.py          loads and validates sops.yaml (pydantic)
  weather.py            Open-Meteo client, time windows, hourly data -> metrics
  llm.py                Groq model wrapper
  intent.py             question -> {activity, groups, place, country, time window}
  matcher.py            which SOPs fire, and in what order
  compose.py            writes the reply (template, or model rewrite)
  guard.py              checks a model-written reply before it is shown
  graph.py              the LangGraph agent
app.py                  Streamlit frontend
cli.py                  terminal frontend
evals/
  run_evals.py          eval suite (19 cases)
  make_fixtures.py      builds synthetic forecasts in Open-Meteo's format
  fixtures/             calm, heatwave, windy, evening storm, rain belt
  RESULTS.md            results of the last eval run
```

---

## How it works

### The graph

```
understand ─┬─ model failed ───────────────────────► cant_understand ─┐
            ├─ off-topic ──────────────────────────► no_guidance ─────┤
            ├─ no location ────────────────────────► ask_location ────┤
            └─► locate ─┬─ place not found ────────► cant_forecast ───┤
                        └─► fetch ─┬─ API down ────► cant_forecast ───┤
                                   └─► match ─┬─ no SOP ► no_guidance ┤
                                              └─► compose ─► verify ──┤
                                                                      ▼
                                                           remember ─► END
```

Each failure is a real branch with its own honest reply. An unknown place and a
dead weather API both end in `cant_forecast`, which says there is no forecast
and gives no advice.

### Where the model is used, and where it isn't

The model is used in exactly two places:

1. **understand**: turns the question into a fixed structure. It can only pick
   activity, group and time-window ids that exist in `sops.yaml`, plus a place
   and country name. Anything off-list is dropped in `intent.py`.
2. **compose**: rewrites the matched SOPs into friendlier sentences.

Everything that decides *what* the user is told (the place, the weather
numbers, which rules fired, their order) is plain Python. The compose step never
sees the user's raw text, only structured facts, so text in the question that
tries to override the rules can at most affect parsing, where the output is
limited to fixed ids.

### Keeping the model honest (`guard.py`)

Every model-written reply is checked before it is shown:

- every number in it must appear in the forecast or the matched SOPs;
- every SOP id it mentions must be one that actually matched, and the main one
  must be mentioned;
- no reassuring phrases ("perfectly safe", "go ahead") next to a warning or
  danger rule.

If any check fails, the reply is thrown away and a deterministic template is
sent instead. The "Based on:" citation line is always added by code, not by the
model.

If the model is unreachable while understanding the question, the bot says it
can't process the question right now rather than guessing what was meant.

### Session memory

LangGraph's `MemorySaver` checkpointer, one thread per chat session. The bot
remembers the last place (with its coordinates), country, activity, groups and
time window, plus a log of each turn. A follow-up like *"what about this evening
instead?"* keeps the place and activity and only changes the time; it doesn't
look the place up again. Forecasts are cached for 10 minutes per location, so
answers within a session don't contradict each other. "New session" in the UI
(or `/new` in the CLI) starts fresh.

### Ambiguous place names

Many place names exist in more than one country. The parser extracts a country
when the user gives one (or it is obvious from a well-known place). If the name
is still ambiguous, the reply says which place was used, e.g. *"several places
are called 'Richmond'; I used Richmond, United States. Tell me the country if
you meant another."* A correction like *"I meant the one in the UK"* looks the
place up again instead of reusing the cached one.

---

## Policies (SOPs)

**Why YAML:** the people who own the advice aren't the people who own the code,
so rules live in a readable data file that is validated on load, not in Python.

A rule reads like a sentence: *for this activity, if this condition, give this
advice at this severity.*

```yaml
- id: TW-01
  title: Strong gusts on a two-wheeler
  category: two_wheeler
  severity: warning                # info < advisory < warning < danger
  activities: [cycling]
  when:
    any:
      - {metric: wind_gust_max, op: ">=", value: 40}
      - {metric: wind_max, op: ">=", value: 30}
  advice: >
    Gusts at this level can push a cycle or scooter sideways ...
```

Conditions support `all` (AND), `any` (OR), which can be nested, and `score` (a
weighted points system for fuzzy rules). Rules apply to listed `activities`, to
`groups` of people, or to everything with `scope: all`.

### The 13 SOPs

| ID | Rule | Severity | Applies to |
|---|---|---|---|
| GEN-01 | Organised heavy rain over the location | danger | everyone |
| GEN-02 | Thunderstorm in the forecast window | danger | everyone |
| GEN-03 | Heat stress for anyone outdoors | warning | everyone |
| EX-01 | Heat stress during exercise | warning | running, cycling, hiking |
| EX-02 | Very high UV during outdoor activity | advisory | running, cycling, hiking, picnic, park |
| EX-03 | No adverse signals for exercise | info | running, cycling, hiking |
| TW-01 | Strong gusts on a two-wheeler | warning | cycling |
| TR-01 | Heavy rain during travel | warning | travel |
| VG-01 | Heat with young children outdoors | warning | children |
| VG-02 | Temperature extremes for elderly people | warning | elderly |
| VG-03 | Hot ground for pets | advisory | dog walks |
| LE-01 | Good conditions for a picnic (fuzzy) | info | picnic |
| LE-02 | Poor conditions for a picnic (fuzzy) | advisory | picnic |

ID prefixes: GEN = general situational, EX = outdoor exercise, TW = two-wheeler,
TR = travel, VG = vulnerable groups, LE = leisure.

**Where the thresholds come from.** Some are published standards: 64.5 mm/day
is IMD's "heavy rainfall" category, UV ≥ 8 is "very high" on the WHO UV index,
and weather codes 95–99 are thunderstorms in the WMO code table. Others (heat
thresholds for children and elderly people, hot pavement for pets, the picnic
weights) are reasonable judgement calls that a domain expert should tune. The
design makes that a YAML edit, not a code change.

### The rain-system rule (GEN-01)

The brief's hardest case is a weather system where no single number looks
extreme. GEN-01 handles this in two ways:

1. `scope: all` and `lead: true`: it applies to every outdoor question, even
   activities with no policy of their own, and it is always shown first.
2. It combines signals instead of using one threshold: 24-hour rain at the IMD
   "heavy" level, **or** moderate 24-hour rain with a very high chance of rain,
   **or** steady rain together with strong gusts. This is why it uses the
   24-hour total (`rain_24h`) and not just the hours asked about.

Limitation: Open-Meteo has no "IMD has flagged a low-pressure area" field, so
this is a proxy built from forecast numbers. The proper fix is an IMD district
warning feed exposed as one more metric.

### The fuzzy rule (picnic)

"Is it a good picnic day" has no single cutoff. LE-01 and LE-02 score five
comfort signals (rain chance counts triple, temperature double, wind, UV and
cloud cover once each) and the total decides good or poor. The weights are
written by the policy owner, not decided by the model, so the same forecast
always gets the same answer.

### When several rules match

Decided in `matcher.py`:

1. Rules marked `lead` (the rain system and thunderstorm) come first.
2. Then by severity, highest first; ties keep the order in the file.
3. "All clear" rules are dropped if anything advisory or worse also matched, so
   the bot never says "no adverse signals" next to a warning.
4. The top rule plus up to two more are shown. Hiding a real secondary risk
   (high UV on a windy day) seemed worse than a slightly longer answer.

### When no rule applies

- **Off-topic** ("should I buy gold?"): answered without fetching any weather.
- **On-topic but nothing fires** (an activity with no policy, or a calm day):
  the reply says no SOP matched, lists the rules it checked, and shows the raw
  forecast numbers, but gives no advice of its own.

The general rules with `scope: all` (heavy rain, thunderstorm, heat) were added
after testing showed that many real questions ("can I attend an event at
Shivaji Park?") had no specific policy. They give weather-based guidance for any
outdoor plan while still keeping all advice inside the policy file.

### Adding a rule live

Append a new block to `sops/sops.yaml` and send the next message. The app
re-reads the file on every message, so no restart and no code change is needed.
A mistake (unknown metric, undeclared activity, duplicate id, malformed
condition) fails loudly with a clear message instead of creating a rule that
silently never fires. The `add_sop_live` eval does exactly this.

---

## Evals

Run with `python evals/run_evals.py`. Results are written to `evals/RESULTS.md`.

19 cases covering:

| Area | Cases |
|---|---|
| SOP clearly applies | two rules at once (TW-01 + EX-02); fuzzy picnic, good day and bad day |
| Paraphrased intent | "pedalling to the office" → cycling; "my little one on the swings" → children |
| Severe weather | rain-belt forecast replayed from a fixture; live Open-Meteo call |
| No SOP applies | off-topic question; outdoor activity with no policy on a calm day |
| Failure paths | weather API down (real HTTP client against a dead port); model down; unknown place |
| Session memory | follow-up keeps context; ambiguous place corrected by country; sessions isolated |
| Adversarial | prompt injection in the question; a deliberately misbehaving model |
| Policy changes | rule added live without code changes; broken rule rejected on load |

**Last run: 17 passed, 2 failed.**

### Known failures

**`severe_replay`: a real grounding gap.**
On a rain-belt forecast the bot picked the right SOP (GEN-01, danger) and gave
the right advice, but the model's reply left out the rain figures (110.4 mm over
24 hours). The brief requires severe answers to be grounded in the real numbers,
so this is a genuine failure. Root cause: whether the numbers appear depends on
the model's wording; the guard checks that numbers aren't invented, but it can't
force the model to include them. Fix: have `compose.py` append the "Why:" line
(the triggering values) in code, the same way the "Based on:" citation is
already added, so grounding no longer depends on the model.

**`ambiguous_place_correction`: the test was wrong, not the bot.**
The test assumed "Mount Everest" is ambiguous, but the parser is allowed to fill
in the country for well-known places, so the model resolved it to Nepal straight
away and there was nothing to flag. Fix: use a name with no obvious default
(e.g. "Richmond", which exists in the US, UK, Canada and Australia) so the
ambiguity path is actually exercised.

### Live weather and the severe case

Live weather changes every day, so the live case can't assert "GEN-01 fires".
It checks things that must hold on any day: the SOPs in the reply equal what the
matcher picks for the same live numbers, every number in the reply came from
that API response, and GEN-01 is first whenever it fires. It then reports
whether a severe event was actually active. The severe path itself is tested on
every run by `severe_replay`, which uses a fixture in Open-Meteo's exact format,
so it doesn't depend on it happening to rain that day.

### Adversarial testing

Two kinds: a prompt injection in the user's question (asking the bot to ignore
its SOPs and confirm a fake policy), and a model that ignores its instructions
(invents a rain figure, cites a non-existent SOP, says "perfectly safe"). The
second felt more important: prompt instructions are probabilistic, while the
guard is the part that has to hold even when the model misbehaves.

---

## Known gaps

- **New kinds of data need code.** New rules, thresholds, activities, groups and
  wording are YAML-only. A rule on something not computed yet (air quality,
  visibility, an IMD warning level) needs a change in `metrics.py` and
  `weather.py`.
- **GEN-01 is a proxy** for an officially flagged weather system (see above).
- **The guard checks facts, not meaning.** It catches invented numbers and
  citations, but a model could soften "postpone" into "maybe consider
  postponing" and still pass. The tone check only catches obvious cases.
- **Time windows are fixed** (morning, afternoon, evening, tonight, tomorrow).
  "At 3pm" becomes "this afternoon".
- **Weather only.** The bot judges weather risk. Questions about crowds, traffic
  or personal safety at an event are outside what any weather SOP can answer.

---

## Use of AI

I used AI (Claude) at multiple places in this project, but I didn't rely on it
completely.

- The **entire frontend** (`app.py`), the **eval suite** (`evals/run_evals.py`)
  and the **entire CLI** (`cli.py`) were generated by Claude.
- For the **SOPs**, I wrote one example rule and Claude generated the rest
  following that pattern.

Testing the bot myself also exposed the ambiguous place name bug (a question about Mount Everest
was answered for a town in South Africa), which led to the country handling.