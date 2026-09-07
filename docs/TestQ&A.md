# NCC 2022 — Manual Q&A test set

Use in Augusta Search with **one volume selected at a time**. Record answer quality, cited clauses, confidence, and response time.

**Agent test loop (automated ask → evaluate → optimize → log):** see [`docs/AGENT-NCC-QA-TEST.md`](./AGENT-NCC-QA-TEST.md).

| Volume | Dataset ID | Scope |
|--------|------------|--------|
| Vol 1 | `ncc2022_vol1` | Class 2–9 buildings (commercial, multi-residential, public) |
| Vol 2 | `ncc2022_vol2` | Class 1 and Class 10 buildings (houses, sheds, carports) |
| Vol 3 | `ncc2022_vol3` | Plumbing Code (water, drainage, stormwater, fire hydrants) |

**Pass criteria (general):** Answer is evidence-bound; cites relevant NCC clauses/tables; distinguishes *when required* vs *how to comply*; notes state variations where applicable; does not invent numbers or clause IDs.

---

## NCC 2022 Volume 1 (`ncc2022_vol1`)

Select **NCC 2022 Volume 1** only. Set **state** in the UI if testing state variations.

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 1 | What are the requirements for a building occupant warning system under the NCC? | Spec 17/18 or S20C7; AS 1670.1; sound in occupied areas; class-specific exceptions (2/3/4, 9a, 9c). |
| 2 | Is smoke detection required in a three-storey Class 5 building? | **Applicability first:** Part E2 DTS, Table E2.2a (or equivalent); then Spec 20/21 if triggered. **Fail** if answer only cites S20C4/S31C16 without yes/no/depends. |
| 3 | When is emergency lighting required in a Class 6 building? | E4 / emergency lighting DTS (e.g. E4D2); storey/room area thresholds; fire-isolated stairways and passageways. |
| 4 | What is the minimum width required for an exit door in a Class 5 building? | Part D3 (construction of exits); door width DTS; may reference disability/access provisions if relevant. |
| 5 | What fire-resistance level (FRL) applies to external walls of a Class 5 building that is Type A construction with a rise in storeys of 4? | Part C (fire resistance); Type A / rise in storeys; C2 DTS or tables; building class applicability. |

### Vol 1 — Set B (questions 6–10)

Second pass after Set A. Covers egress travel, fire fighting, access, stairs, and Section J — different NCC parts than Set A.

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 6 | What is the maximum travel distance to an exit in a Class 5 building? | Part D1/D2 (access and egress); travel distance DTS or **table**; class and building configuration. **Fail** if only generic exit definitions without a distance (m). |
| 7 | Is a fire hydrant system required in a Class 6 building? | Part E1 DTS + **applicability table** (when hydrants required); not only Spec 17 installation detail. **Fail** if only S17C* “how to install” without yes/no/depends for Class 6. |
| 8 | What accessibility requirements apply to the entrance of a Class 2 building? | Part D4 (access for people with disability); accessible entrance/path DTS; may cite AS 1428.1 where referenced. |
| 9 | What are the maximum riser and minimum going dimensions for stairs in a Class 5 building? | Part D3 (construction of exits); stair dimensions DTS or table; numeric mm limits. **Fail** if insufficient_evidence or classification-only (A6G6). |
| 10 | What are the Section J energy efficiency requirements for a Class 5 office building? | Part J (J1–J8 as applicable); DTS for lighting, HVAC, or fabric; NatHERS/verification path if in evidence. **Fail** if answer mixes Vol 2 housing H-part only. |

### Vol 1 — Set C (questions 11–15)

Third pass. Covers sprinkler applicability, fire-isolated stairs, fire doors, smoke control, and sanitary facilities — parts not covered in Sets A/B.

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 11 | Is a sprinkler system required in a four-storey Class 5 office building with a total floor area of 2,500 m²? | Part E1 DTS + **applicability** (when sprinklers required); rise in storeys and/or floor area triggers. **Fail** if only Spec 17/S17C* installation detail without yes/no/depends for this scenario. |
| 12 | When is a fire-isolated stairway required in a Class 5 building? | Part D1/D3 (access and egress); triggers such as rise in storeys, effective height, or building configuration. **Fail** if answer only gives stair dimensions (riser/going) without addressing *when* fire isolation is required. |
| 13 | What fire-resistance level applies to a fire door in a fire-isolated exit? | Part C2/C3 (fire resistance / fire protection); door FRL DTS or table (e.g. -/60/30 style). **Fail** if answer cites wall FRL only or generic C1 intro without door-specific FRL. |
| 14 | Is mechanical smoke exhaust required for a basement carpark in a Class 7 building? | Part E2 (smoke control); basement/carpark or enclosed carpark triggers; E2 DTS or applicability table. **Fail** if only Part F4 natural ventilation without smoke-control applicability. |
| 15 | What are the minimum sanitary facility requirements for a Class 5 office building? | Part F4 (sanitary and other facilities); minimum numbers or ratios per occupancy/table; may reference D4 accessible facilities. **Fail** if insufficient_evidence or classification-only without facility counts or ratios. |

---

## NCC 2022 Volume 2 (`ncc2022_vol2`)

Select **NCC 2022 Volume 2** only. Set **state** if testing bushfire or state-specific housing provisions.

NCC 2022 Vol 2 uses **H-parts** (H1 structure, H3 fire/smoke alarms, H4 health amenity, H6 energy, H7 bushfire). Pass criteria reference these — not legacy Vol 2 B/C/F part names.

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 1 | What are the bushfire construction requirements for a Class 1 building in a Bushfire Attack Level 12.5 (BAL-12.5) area? | Part **H7** DTS (H7D*); BAL-specific construction (roof, walls, openings); AS 3959 where cited. **Fail** if only H7P* performance objectives without construction detail. |
| 2 | What is the minimum ceiling height required for a habitable room in a Class 1 building? | Part **H4** room heights (H4D*); habitable room definition; minimum height in metres (e.g. 2.4 m). **Fail** if insufficient_evidence or H7/H2 unrelated clauses only. |
| 3 | Is a smoke alarm required in a Class 1a dwelling under the NCC? | Part **H3** DTS (H3D*); **applicability** (yes + where) then location/interconnection. **Fail** if insufficient_evidence or H7 bushfire clauses only. |
| 4 | What bracing is required for a timber-framed Class 1 building? | Part **H1** structure (H1D6, H1D*); bracing walls; Housing Provisions bracing tables. **Fail** if H7 bushfire or insufficient_evidence. |
| 5 | What are the energy efficiency requirements for the building fabric of a new Class 1 house? | Part **H6** (H6D*, Housing Provisions Part 13); insulation, sealing, thermal breaks; NatHERS/verification if in evidence. |

### Vol 2 — Set B (questions 6–10)

Second pass. Covers wet areas, light/ventilation, pools, stairs, and NatHERS path — different H-parts than Set A.

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 6 | What waterproofing requirements apply to the floor of a bathroom in a Class 1 building? | Part **H4** wet areas (H4D2, H4D3); floor/wall waterproofing DTS; AS 3740 where cited. **Fail** if H7 bushfire, H6 energy, or insufficient_evidence. |
| 7 | What are the natural light and ventilation requirements for a habitable room in a Class 1 building? | Part **H4** or **H5** DTS; window/openable area or ventilation rates; numeric limits or table values. **Fail** if only performance objectives (H*P*) without measurable requirements. |
| 8 | What are the safety barrier requirements for a swimming pool on a Class 1 property? | Part **H7** pool provisions (H7D2*); barrier height, gates, or AS 1926 reference. **Fail** if only H7 bushfire (BAL) clauses without pool barrier detail. |
| 9 | What are the maximum riser and minimum going dimensions for stairs in a Class 1 building? | Part **H5** safe movement (H5D*) or Housing Provisions; numeric mm limits for riser/going. **Fail** if insufficient_evidence or Vol 1 D-part clauses only. |
| 10 | What energy efficiency provisions apply when using the NatHERS compliance path for a new Class 1 house? | Part **H6** verification (H6V*, S42C*); minimum star rating (e.g. 7 stars) and conditions; not only H6D fabric DTS. **Fail** if answer repeats Q5 fabric DTS without addressing the NatHERS/VM path. |

### Vol 2 — Set C (questions 11–20)

Third pass. Ten additional topic types for agent regression (structure, roof, glazing, decks, termites, weatherproofing, Class 10, fire separation, condensation, livable housing).

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 11 | What footing and slab requirements apply to a Class 1 building on reactive clay soil? | Part **H1** (H1D4, H1D5); AS 2870 or Housing Provisions footings/slabs. **Fail** if H7 bushfire or insufficient_evidence. |
| 12 | What roof cladding requirements apply to a Class 1 building in a cyclonic wind region? | Part **H1** (H1D7, H1D8); cyclonic fixing/cladding DTS. **Fail** if unrelated energy or bushfire only. |
| 13 | What are the external glazing requirements for energy efficiency in a new Class 1 house? | Part **H6** (H6D2, Housing Provisions Part 13.3); glazing/shading U-value or DTS path. |
| 14 | What construction requirements apply to a deck or balcony attached to a Class 1 building? | Part **H1** (H1D11); bracing/lateral restraint; Housing Provisions deck clauses. |
| 15 | Is termite management required for a Class 1 building in a termite risk area? | Part **H1/H2** termite DTS or risk-based triggers; **applicability** (yes/depends) before detail. |
| 16 | What damp-proofing and weatherproofing requirements apply to external walls of a Class 1 building? | Part **H2** (H2D*); damp course, weatherproofing — not bathroom wet areas (H4D2). |
| 17 | What NCC provisions apply to a Class 10a carport attached to a Class 1 dwelling? | Class 10 + attachment rules; relevant **H1** structure or separation provisions. **Fail** if Vol 1 commercial parts only. |
| 18 | What fire separation is required between a Class 1 dwelling and an attached private garage? | Part **H3** fire separation / smoke alarm boundary provisions. **Fail** if pool or bushfire H7 only. |
| 19 | What condensation management requirements apply to a Class 1 building? | Part **H6** or Housing Provisions condensation/vapour permeability where cited. |
| 20 | What livable housing design requirements apply to a new Class 1 house? | Part **H8** (H8D*, H8P*); silver/gold levels or step-free access if in evidence. **Fail** if D4 Vol 1 accessibility only. |

---

## NCC 2022 Volume 3 (`ncc2022_vol3`)

Select **NCC 2022 Volume 3** only. Plumbing Code — water services, drainage, stormwater.

| # | Question | What a good answer should touch |
|---|----------|----------------------------------|
| 1 | What are the requirements for backflow prevention on a cold water service? | Part B cold water; backflow prevention device; contamination risk; referenced standard if cited. |
| 2 | What is the maximum allowable temperature for delivered heated water at sanitary fixtures in a Class 2 building? | Part C heated water; temperature limits; scalding prevention; class/building context. |
| 3 | What are the requirements for sanitary drainage pipe sizing for a single dwelling? | Part E sanitary drainage; pipe size / gradient DTS or tables; fixture unit method if in evidence. |
| 4 | When is an overflow relief gully required for a sanitary drainage system? | Sanitary drainage installation (Part E2 or equivalent); ORG purpose and location rules. |
| 5 | What are the requirements for fire hydrant installation on a property? | Part H fire hydrant; hydrant location, coverage, or connection to water supply; may reference building class or site conditions. |

---

## Test log template

Copy one block per run:

```
Date:
Volume / dataset_id:
State filter (if any):
Question #:

Response time (approx):
Confidence shown:
Citations returned:

Pass / Partial / Fail:
Notes:
```

**Fail examples:** Wrong volume content; invented clause IDs; answers *how* to comply without addressing *whether required*; ignores Table E2.2a-style applicability when question asks “is X required?”

**Partial examples:** Correct spec clauses but missing applicability table; correct theme but missing state variation note when state filter set.
