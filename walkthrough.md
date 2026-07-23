# Immutable layout-candidate and canonical role-binding pipeline

## Root causes

The immutable `LayoutCandidate` work fixed split ownership after topology generation, but the failing 2BHK request exposed an earlier identity gap.

`intent_compiler._resolve()` resolved an exact ID or a unique room type and otherwise returned the symbolic token unchanged. User-origin constraints were deliberately retained even when the token did not exist. Consequently, `master_bathroom` crossed into `hard_topology_errors()`, which treated the unresolved internal alias as a defect in every generated candidate. Sixteen structurally different graphs could therefore be rejected by the same pre-existing alias before CP-SAT ran.

Three related semantic defects amplified the failure:

- bathroom pairing was list-order based and did not persist one canonical `owner_room_id`;
- the private-transit exception depended on an exclusive relation being present, so a missing ownership relation made a valid owner-to-ensuite leaf look like unrelated bedroom transit;
- semantic count repair retained duplicate utility and corridor suggestions. “Minimal corridor space” was interpreted as rooms instead of an objective.

A separate regression surfaced during verification: topology edge deduplication used only the unordered room pair. An `adjacent` relation could replace a necessary access edge for the same rooms, while topology BFS counted all edges—including adjacency—as traversable. Edge identity now preserves adjacency and access independently, and graph reachability uses access intents only.

## Correct lifecycle

The fresh-generation order is now:

1. Gemini analysis and deterministic semantic count repair.
2. Canonical room-ID creation.
3. Deterministic bedroom and bathroom role assignment.
4. Exact attached-bathroom ownership assignment.
5. Optional-room and stale default-relation normalization.
6. Circulation-intent compilation.
7. Synthetic circulation generation only when the program needs it.
8. Symbolic selector resolution to canonical room IDs.
9. Relation endpoint invariant audit.
10. Multi-family topology generation and hard graph gate.
11. Pareto ranking with a low-complexity baseline first unless the user explicitly requests another topology.
12. CP-SAT edge compilation and solved-edge audit.
13. Immutable `MasterBlueprint` handoff and geometry-hash verification.
14. `PairedDoor` realization and final BFS/private-transit validation.
15. Job-scoped artifact publication and stale-result frontend gating.

## Role-binding contract

`bind_room_roles()` is the pre-topology identity boundary. It preserves existing IDs and creates missing IDs deterministically. For the failing request it produces:

- `master_bedroom-1`: `type=master_bedroom`, `role=master`;
- `bedroom-1`: `type=bedroom`, `role=standard`;
- `bathroom-1`: `role=attached`, `owner_room_id=master_bedroom-1`;
- `bathroom-2`: `role=common`, `owner_room_id=null`;
- `utility-1`: explicit-user provenance and required;
- `corridor-1`: topology-synthesized provenance and not user-required.

An attached bathroom is always a terminal leaf connected only to its exact canonical owner. Role and ownership do not depend on display names. `reassign_bathroom_owner()` updates a modification atomically, removes old common-access metadata, creates the new exclusive owner relation, and preserves unrelated IDs.

## Selector-resolution contract

Role selectors such as `master_bedroom`, `master_bathroom`, `attached_bathroom`, `common_bathroom`, `utility`, and `corridor` are resolved after room roles and synthetic circulation exist. Multi-instance selectors expand deterministically across exact IDs where the relationship semantics permit it.

Every compiled constraint and serialized `SpatialRelation` retains `original_source_selector` and `original_target_selector` for logs, while `source`/`target` and `source_room_id`/`target_room_id` contain canonical IDs only.

Before topology generation, `assert_relation_endpoints()` verifies every endpoint against the canonical room map. A missing user-authored endpoint raises `InternalInvariantError` with the relation ID, original selector, attempted ID, available IDs, and program/candidate ID. Candidate generation never loops over multiple graphs for one unresolved alias. Architectural-default relations to an optional room that was intentionally pruned are removed or rebound to the actual entry before this audit.

## Circulation and utility semantics

An explicitly singular utility normalizes duplicate model suggestions to one room. Two separately named functions—such as laundry and storage utilities—remain distinct and keep purpose/provenance.

“Minimal corridor space” and “very little corridor space” compile to a strong `circulation_area/minimize` optimization preference. They do not create a user corridor count. A synthesized corridor’s realized area contributes to both `circulation_cost` and, when minimization was requested, `dead_space` cost.

## Ownership-aware private transit

The only bedroom-transit exception is:

`entry/circulation -> owner bedroom -> terminal attached bathroom`

The bathroom must have `role=attached`, its `owner_room_id` must name that bedroom, and its realized paired-door neighbors must equal `{owner_room_id}`. Blocking the owner may strand only those verified terminal ensuites. Bedroom routes to a common bathroom, unrelated room, utility, corridor, or non-terminal attached bathroom remain invalid. Both topology hard validation and final paired-door validation apply the same semantic rule.

## Preserved immutable pipeline

The fix does not weaken the hard topology gate, CP-SAT audits, geometry hashes, Pareto search, `PairedDoor` authority, final BFS, or job-result gating. `near` and `adjacent` do not become doors; access, adjacency, reachability, ownership, and optimization preferences remain distinct typed concepts.

## Regression coverage

`test_topology_pipeline.py` now covers:

- the exact 40×40 failing prompt through semantic repair, canonical binding, 16 generated candidates, multiple hard-feasible families, CP solve, paired doors, and successful final validation;
- one master/one standard bedroom, one attached/one common bath, exact ownership, one utility, and synthesized minimal circulation;
- no attached bathroom, without inventing a master-bath relation;
- two distinct ensuites plus one common powder room;
- compact 3BHK circulation minimization without duplicate corridors;
- one utility and two explicitly distinct utility functions;
- near-common-bath proximity without an invented living-room door;
- explicit central-hub topology remaining legal;
- duplex floor-local ownership resolution;
- atomic common-to-attached bathroom modification with stable unrelated IDs;
- immediate invariant failure for a genuinely missing user endpoint;
- coexistence of adjacency and access semantics for the same room pair.

Existing multi-floor, courtyard, edit-transaction, geometry-hash, and frontend stale-result regressions continue to pass.

## Verification results

Commands run:

```text
.\venv\Scripts\python.exe -m unittest -v test_topology_pipeline.CanonicalRoleBindingRegressionTests.test_exact_failing_prompt_reaches_fully_validated_layout
.\venv\Scripts\python.exe -m unittest discover -v
node --test test_job_result_gate.mjs
npm run build
.\venv\Scripts\python.exe -m py_compile candidate_contract.py constraint_schema.py intent_compiler.py topology_generator.py topology_grammar.py topology_optimizer.py layout_scorer.py final_validator.py cloud_extractor.py server.py test_topology_pipeline.py
```

Final results:

- exact failing-prompt end-to-end regression: 1/1 passed;
- complete Python suite: 66/66 passed;
- frontend job-result tests: 2/2 passed;
- production frontend build: passed;
- changed-module Python compilation: passed.

No genuine regression remains in the verified suite. The only warning is the existing optional physics-model notice. `git diff --check` continues to report trailing whitespace in unrelated pre-existing user edits in `geometry_validator.py` and `room_planner.py`.
