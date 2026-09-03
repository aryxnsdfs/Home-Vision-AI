# Architecture

How Home Vision AI turns a sentence into a buildable floor plan, which module
owns which decision, and the invariants that hold the whole thing together.

Live at https://sanky.space.

---

## 1. The central idea

A language model decides **what the house needs**. A constraint solver decides
**where everything goes**. Giving both jobs to one model is why AI floor plans
usually look plausible and are quietly unbuildable.

A language model is excellent at reading "a house for my parents" as implying an
accessible bedroom on the ground floor, and hopeless at guaranteeing that bedroom
has a door. A constraint solver is the exact opposite. So there is a typed
contract between them, and neither is trusted with the other's job.

```
prompt
  │
  ▼  cloud_extractor  ─── Gemini, schema constrained
architectural program        (which rooms, how many, which floor, what connects)
  │
  ▼  intent_compiler   ─── typed spatial constraints
IntentContract
  │
  ▼  topology_generator / topology_optimizer
access graph candidates      (compact hub / linear spine / split branches, ranked)
  │
  ▼  geometry_engine   ─── OR-Tools CP-SAT on a quarter-foot grid
solved rectangles
  │
  ▼  layout_engine     ─── walls, doors, windows, furniture
  ▼  mep_generator     ─── electrical circuits, plumbing runs
  ▼  final_validator / geometry_validator   ─── hard gates
  │
  ▼
house  →  3D scene, plans, 10 page engineering PDF
```

---

## 2. Runtime shape

**Backend.** FastAPI, one large `server.py` (~8.2k lines) holding the pipeline
orchestration and the HTTP surface, with the real work delegated to focused
modules.

**Frontend.** React + Three.js through react-three-fiber for the 3D view,
Zustand for state, Tailwind for styling, react-pdf for the engineering export.
23 source files under `src/`.

**Transport.** `POST /api/generate/stream` streams progress over server-sent
events. The client shows nine stages as they complete.

**Deployment.** `/var/www/sanky` on Ubuntu behind nginx, service
`sanky-backend` (uvicorn on 127.0.0.1:8000). The directory is not a git
checkout and hosts other apps, so deployment extracts a tarball of the changed
Python modules plus a locally built `dist/` over it.

**No queue required.** Redis and Celery are supported but deliberately not
installed — the box has around 1.1 GB free RAM. `queue_available()` detects the
missing broker and runs the pipeline in-process, streaming identically. A
missing broker should never be why someone cannot get a house.

### API surface

| Endpoint | Purpose |
| --- | --- |
| `POST /api/generate/stream` | The main event: prompt to house, streamed |
| `POST /api/generate` | Same pipeline, single response |
| `POST /api/generate-wiring` | Electrical layer for an existing project |
| `POST /api/generate-plumbing` | Water supply and drainage layer |
| `POST /api/generate-structural` | Beams and columns |
| `POST /api/analyze-prompt` | Clarifying questions for a vague brief |
| `POST /api/recalculate-cost` / `GET /api/cost-presets` | Estimation |
| `POST /api/template` / `/api/template/stream` | Saved starting points |
| `GET /api/health` | Liveness |

---

## 3. The pipeline, stage by stage

### Stage 1 — Language to program (`cloud_extractor.py`, `llm_pool.py`)

Gemini turns the sentence into a typed architectural program: which rooms, how
many, which floor each belongs on, what connects to what, whether a bathroom is
an ensuite or shared. The output is schema constrained, so what comes back is
structured data rather than prose to parse and hope about.

`llm_pool.py` is the single entry point for every model call. It holds a pool of
API keys and rotates on quota or 5xx errors, parks a failing key on a cooldown,
classifies errors as retryable or key-fatal, and caches identical requests so a
retry does not pay twice. A `BYPASS_CACHE` ContextVar forces a fresh answer when
a retry follows a bad program.

Unknown room types get their minimum dimensions inferred once and cached, so a
home theatre is sized as a home theatre rather than falling back to a generic
box.

### Stage 2 — Program to constraints (`intent_compiler.py`)

Language relationships become typed geometry objectives, never door edges by
accident. `compile_intent()` produces an `IntentContract` of
`ArchitecturalConstraint` records with a kind (DIRECTION, NEAR, ADJACENT,
DIRECT_CONNECTION, SEPARATION, …), a strength, and an origin.

`recover_prompt_directions()` reads compass placements straight out of the
brief. Directions used to reach the solver only when the model happened to list
them, so the same prompt honoured "kitchen in the southeast" on one run and
ignored it on the next.

### Stage 3 — Program to topology (`topology_generator.py`, `topology_optimizer.py`)

Before any coordinates exist, several structurally different access graphs are
generated for the same room list: a compact hub, a linear spine, split public and
private branches. `topology_optimizer.py` ranks them on circulation cost,
privacy, zoning and daylight. The best few go forward to geometry.

### Stage 4 — Topology to geometry (`geometry_engine.py`)

OR-Tools CP-SAT. Every room becomes a rectangle with variables for position and
size on a quarter-foot grid (`COORD_SCALE = 4`).

**Hard constraints** — rooms must not overlap; everything stays inside the
buildable envelope after setbacks; every required door needs a minimum length of
shared wall between the two rooms (`MIN_DOOR_WALL_FT = 4.0`, with a 3 ft minimum
overlap on the touch constraint); minimum dimensions per room type; forbidden
adjacencies, so a toilet does not open onto a kitchen.

**Soft objectives** — compactness, circulation cost, room size preferences,
daylight, and compass placement.

The engine tries a series of candidate envelopes (snug first, then progressively
roomier, then the whole plot) under a wall-clock budget carried in a
`SOLVE_DEADLINE` ContextVar, so a single request cannot burn the whole budget on
near-identical alternatives.

### Stage 5 — Geometry to a house (`layout_engine.py`, `mep_generator.py`)

Finite wall segments are derived from the solved rectangles. Doors and windows
are fitted to the specific wall segment they belong to and shrink or are skipped
when a wall is too short. Furniture is placed. `mep_generator.py` routes
electrical circuits from a distribution board through per-room switchboards, and
plumbing from source through underground tank, pump, overhead tank and manifold
to every fixture.

### Stage 6 — Validation gates (`final_validator.py`, `geometry_validator.py`)

Nothing reaches the user unchecked. The validator walks the **realised** door
graph, not the intended one, and rejects any layout where a room is unreachable,
where a private or wet space is the only route to another room, or where a
dimension has fallen below usable. A failed layout is discarded and another
topology candidate is tried.

---

## 4. Coordinate spaces

The single most common source of "things are floating outside the house" bugs.

| Value | Space |
| --- | --- |
| `room.x`, `room.z` | world / absolute |
| `room.doors[].x/z`, `room.windows[].x/z` | **room-local** |
| `mep_nodes[].x/z`, MEP path endpoints | world / absolute |

Compass convention: **north = low z, south = high z, west = low x, east = high x.**

---

## 5. The program contract

The invariant that replaced a growing pile of per-room-type rescues.

Rooms went missing between the accepted program and the finished drawing, and
each time one was noticed a separate rescue got written for that room type:
parking, then study, then pooja, then bathrooms. Each rescue also had to be
written twice, because the branch handling an explicit floor schedule and the
branch without one assemble the program separately.

Running the extraction directly showed the premise was wrong. Asked for "a home
gym and a study room near the bedrooms", the model returns both — in
`target_rooms`, in `floor_program` and in `unassigned_rooms` — along with the
garage. **Nothing is dropped at extraction.** The rooms were lost downstream, in
floor assembly, duplex splitting, shedding and pruning, none of which had to
account for what it removed.

So there is one contract, gathered once, covering the three things that can
genuinely put a room in it:

1. **The accepted program** — what the model returned.
2. **Rooms the brief named** that the program somehow lacks, for the runs where
   the model does drop one.
3. **Building requirements** no program states — a house needs a bathroom
   whether or not anyone asked for one. The requested BHK lives here too and
   overrides whatever the model returned.

`program_contract(specs, prompt, bhk)` builds it. `reconcile_against_contract()`
diffs it against what the floors and site actually hold, and is called **once**,
at the point where the two assembly branches rejoin. Rooms shed on purpose are
recorded in the `_SHED_TYPES` ledger as they go and left alone — the user already
sees those as a warning. Everything else went silently and is put back.

**Only bedrooms are ever trimmed**, and only from the floor actually carrying the
surplus. Everywhere else a count above the contract means a later stage added a
room deliberately (`ensure_circulation` adds the corridors a big program needs),
and removing those undoes work the plan depends on.

`_program_room_class()` is the one notion of room identity, used by the product
**and** by the acceptance suite: `office` and `study_room` are one concept, so
are `garage` and `parking`. Extend it rather than adding a lookup elsewhere.

Two concepts are kept deliberately **distinct**, and both were caught only by the
existing root-level tests:

- `family_lounge` is not `living_room` — a duplex has a living room downstairs
  and a lounge upstairs, and merging them made the upstairs one look like a
  duplicate and lose its place.
- `prayer_room` is not `pooja_room` — `test_room_intelligence` pins this.

### The standard this enforces

The plan either contains everything it owes, **or the user is told exactly what
could not fit.** Never a silent drop. On the hardest test prompt the warnings
read:

> Restored 1x gym that the layout program had dropped.
> Plot is tight for the full program, so bathroom, pooja room, staircase,
> utility was left out to keep every requested room at a usable size.

---

## 6. Compass placement

A pin could be lost in three independent places, which is why fixing one and
re-testing kept looking like it had worked.

1. **Extraction never emitted it** — fixed by `recover_prompt_directions()`.
2. **The hard constraint was relaxed away** — pins were hard half-planes, and on
   infeasibility a recovery pass dropped all of them. They are reified soft
   constraints now (`DIRECTION_MISS_PENALTY`), so they can never cause
   infeasibility and that recovery is gone.
3. **Recovery solves had no objective at all** — `model.Minimize()` was skipped
   entirely on a recovery pass to publish a feasible incumbent quickly, which
   silently discarded the compass. It now minimises the direction terms alone.

**Both a discrete penalty and a linear pull are required.** The penalty decides
which half of the plot; the linear term gives CP-SAT a gradient to follow. With
only the penalty, a four-second solve never flips the booleans and results get
*worse*. With only the pull, the weights multiply a coordinate and reach ~77,000
— they bulldoze every other objective and the house sprawls into the corners.

Pins aim `DIRECTION_MARGIN_CP` (8 = one foot) past the midline, because a room
centred exactly on the line sits in both halves and the proportional expansion
that fills the plot afterwards can nudge it into the wrong one.

To diagnose: print `floor_data['_hard_direction_count']` from
`_solve_single_topology`. **pins = 0 means the loss is upstream in extraction,
not in the solver.**

---

## 7. Failing honestly

When the model is unavailable the system says so, rather than quietly handing
over a generic house. Two bugs used to make a quota outage look like a layout
bug:

- A route handler declared `async def analyze_prompt` further down `server.py`
  **shadowed** the `analyze_prompt(prompt)` helper defined above it. Every LLM
  failure falls back to that helper, so the fallback called the coroutine and
  died with `'coroutine' object is not subscriptable`. The endpoint is now
  `analyze_prompt_endpoint`; the URL is unchanged. `server.py` is one huge
  module and route handlers are often named after the helpers they wrap — watch
  for this pattern generally.
- With that fixed, the keyword fallback read "4BHK duplex with a pooja room in
  the northeast" as *one bedroom*, so the user was told "BHK mismatch: requested
  4, generated 1" — blaming the layout engine for a billing problem. An
  extraction failure now reports itself.

---

## 8. Module map

| Path | What lives there |
| --- | --- |
| `server.py` | FastAPI app, generation pipeline, program contract, streaming |
| `llm_pool.py` | Key rotation, retries, response cache, schema-constrained calls |
| `cloud_extractor.py` | Prompt to architectural program, room sizing, topology wiring |
| `intent_compiler.py` | Typed spatial constraints, compass recovery, room roles |
| `topology_generator.py` / `topology_optimizer.py` | Access graph candidates and ranking |
| `topology_grammar.py` | Structural vocabulary for those graphs |
| `geometry_engine.py` | CP-SAT room placement |
| `layout_engine.py` | Walls, doors, windows, furniture, fallback layout |
| `room_planner.py` | Room-level planning helpers |
| `mep_generator.py` / `mep_rules.py` / `mep_router.py` | Electrical and plumbing |
| `final_validator.py` / `geometry_validator.py` | Hard validation gates |
| `layout_scorer.py` | Objective vector used to rank candidates |
| `candidate_contract.py` | `LayoutCandidate`, `RoomProvenance`, invariants |
| `semantic_evaluator.py` / `semantic_models.py` | Open-vocabulary room semantics |
| `structural_generator.py` | Beams and columns |
| `cost_engine.py` | Material and cost estimation |
| `edit_intelligence.py` | "Modify layout" on an existing project |
| `asset_library.py` | Furniture, site features, outdoor/custom room recovery |
| `vocabulary.py` / `matcher.py` / `local_extractor.py` | Local NLP fallback |
| `blueprint_renderer.py` | Runtime blueprint PNGs |
| `celery_worker.py` | Optional queued generation |
| `src/` | React front end and the 3D viewer |
| `src/pdf/ArchitectReport.jsx` | The ten page engineering export |

---

## 9. Tunables

All read from the environment with the defaults below.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GEMINI_API_KEYS` | — | Comma-separated rotation pool |
| `GEMINI_MODEL` | `gemini-flash-latest` | Use the alias, not a pinned version |
| `GEMINI_THINKING_BUDGET` | `0` | Thinking off; these calls fill a strict schema |
| `LLM_KEY_COOLDOWN_SECONDS` | `300` | How long a failing key is parked |
| `LLM_CACHE_ENTRIES` / `LLM_CACHE_TTL_SECONDS` | `128` / `900` | Response cache |
| `CP_SOLVER_TIMEOUT_SECONDS` | `4` | Per-solve cap |
| `CP_SOLVER_RESCUE_SECONDS` | `3` | Rescue solve cap |
| `CP_SOLVER_WORKERS` | `8` | CP-SAT parallelism |
| `GEOMETRY_BUDGET_SECONDS` | `8` | Whole geometry phase |
| `DIRECTION_MISS_PENALTY` | `25000` | Cost of breaking a compass pin |
| `DIRECTION_MARGIN_CP` | `8` | One foot clear of the midline |
| `ROOMS_PER_CIRCULATION_HUB` | `6` | Rooms one corridor can front |
| `PROGRAM_FIT_COVERAGE` | `0.75` | Site coverage the program is fitted to |
| `PROGRAM_FIT_SLACK` | `0.88` | Slack reserved for circulation |
| `FINAL_ROUND_MAX_ROOMS` | `11` | Crowding cap on the last relaxation round |
| `UPPER_FLOOR_COVERAGE` | `0.85` | Upper floor against the ground slab |
| `VERTICAL_ESCALATION_MARGIN` | `1.15` | When to push rooms to a new floor |
| `RELAXATION_BUDGET_SECONDS` | `240` | Total wall clock across retries |
| `GENERATION_BUDGET_SECONDS` | `600` | Hard ceiling on one request |
| `MIN_ROOM_FRONTAGE_FT` | `8` | Minimum usable frontage |

---

## 10. Known limits

- **Very dense single floors** used to fail door adjacency on a plot they filled
  a third of, and this section used to call that an inherent corridor-perimeter
  limit. It was not. Every topology funnelled the whole private and semi public
  program onto the *first* corridor, `ensure_circulation` counted the foyer as a
  hub and so provisioned no extra corridors, and CP-SAT holds a corridor to 5 ft
  wide - so eleven doors needed a 41 ft corridor. Circulation is now distributed
  across every hub the program is given, with the hubs chained so they stay
  reachable. The perimeter argument is real but it binds far later than it did.
- **Rare enclosed voids.** Roughly one layout in nineteen leaves a small pocket
  of dead space walled in on every side. `dead_space` is a soft objective, not a
  hard constraint.
- **Compass placement is very good, not perfect.** It is priced dominantly but
  remains a soft constraint, so it yields when the alternative is no house.
- **Model availability is the binding constraint.** One healthy key is not
  enough: a single generation makes several model calls, so one key hits its own
  per-minute rate limit partway through. Two or three keys with live quota is the
  practical minimum.

---

## 11. Testing

Root-level tests, run with `pytest`:

| File | Covers |
| --- | --- |
| `test_program_contract.py` | The contract invariant, aliasing, bedroom trimming |
| `test_room_intelligence.py` | Program fidelity, open vocabulary, duplex schedules |
| `test_semantics.py` | Open-vocabulary room semantics, predicates |
| `test_topology_pipeline.py` | Candidate contract and Pareto selection |
| `test_graph.py` | Access graph behaviour |

The existing tests encode decisions that are not obvious from the code — the
`family_lounge` and `prayer_room` distinctions above were both caught only by
running them. **Run the whole root suite before trusting any change to room
identity or the contract.**

Beyond unit tests there is an acceptance sweep of twenty briefs covering
directions, adjacency, floors, MEP containment and plumbing service. Results are
stochastic, so a single successful run proves nothing: compare across repeated
runs before believing a change helped.

## 12. Licence

MIT. See `LICENSE`.

---

## Appendix — what changed in this pass

Twenty acceptance briefs, from a plain 2BHK to a maximum-difficulty duplex with
compass pins, adjacency requirements, two floors, a gym, a study, a pooja room,
a staircase and parking.

**Clean results: 5/20 → 17/20.** Compass failures across the sweep: 11 → 0.

### The commits

| Commit | What it did |
| --- | --- |
| `bba778a` | Compass placement honoured; stopped losing requested rooms |
| `e0254be` | Stopped the API route shadowing the local-NLP fallback |
| `0b90d5c` | Report model unavailability instead of blaming the layout |
| `70069fb` | Merged the divergent GitHub history into the deployed line |
| `370bf91` | One program contract in place of per-room-type rescues |

### Compass placement

Pins were being lost in three independent places — extraction never emitting
them, a recovery pass dropping all of them on infeasibility, and recovery solves
running with no objective at all. All three are closed, and the weighting was
rebalanced so a discrete penalty decides the half while a linear pull gives the
search a gradient. See §6.

### Requested rooms

The recoveries for site features and custom rooms only ran on the code path
without a floor schedule, so every duplex and most detailed briefs skipped them.
Shedding also targeted the *largest* rooms even when only the room-count cap
bound, which went straight for the requested gym and dining room while keeping
small invented ones. Duplicate collapse was driven by a hardcoded
`SINGLETON_ROOM_TYPES` list and is now a rule based on whether the brief asked
for one. All of this was then subsumed by the program contract in §5.

### Robustness

- Circulation scales with every room that opens off it, not bedrooms and baths
  alone — a 17-room floor was being given two hubs.
- `layout_scorer` no longer dies with a `KeyError` when the door graph names a
  room a relaxation round had shed.
- The requested BHK is authoritative over the model's count, which is what a
  3BHK returning four bedrooms had been doing to the semantic gate.

### Test coverage

`test_program_contract.py` added — 17 cases pinning the contract invariant, the
concept aliasing, and bedroom trimming. All 68 pre-existing root tests pass.

### Measurement notes

Results are stochastic and quota-sensitive. Two of the three remaining failures
in the final sweep were "no topology candidate" during 135 key rotations, and
both prompts passed cleanly in quieter runs — read them as quota rather than
logic. Twice during this work a change that felt obviously correct measured
*worse* on a proper A/B and was backed out. Compare across repeated runs.
