# The test suite: what it costs, and what shrinking it would buy

2026-08-17. Asked to get 6,249 tests "under 1000 without losing fidelity", on
the stated grounds that "I can guarantee we have duplicates".

Measured instead of argued. The short version: **there are almost no
duplicates, test count and CI cost are nearly decoupled, and the one metric
that says otherwise would delete the guards for this project's worst bugs.**

## 1. Duplicates: 4.0%, and most of those are not duplicates

Every test body AST-normalised — docstrings stripped, locals renamed to
positional placeholders, constant values blanked — then clustered by structure:

```
4,499 test functions -> 4,396 distinct structures
in a cluster of >1  : 182 (4.0%)
removable           : 103
```

And the largest clusters are same-shape/different-input: `test_primes` beside
`test_the_true_minus_sign`, four `test_comhost_*_get_each_method` over four
different device maps. Distinct cases, not copies.

Supporting shape: **test LOC is 1.20x source LOC** (91,802 / 76,819), median
test is 2 assertions and 5 statements. That is lean. The 2026-07-18 audit found
the same thing at 1,154 tests (~1.5-2.5% removable) and it still holds at 5.4x
the size.

## 2. Count and cost are decoupled — demonstrated, not asserted

`test_flows_wizard.py` was the biggest file in the suite by test id: **811 ids
from 400 lines**, four parametrised tests asking four questions of the same 192
generated graphs. Consolidated to one test per answer asking all four of a graph
generated once — assertions collected rather than raised, so every fault still
reports individually, and two properties previously checked only on the default
target are now checked on all three.

**811 -> 235 ids. 0.96s -> 0.55s.**

576 test ids removed bought four tenths of a second.

The distribution says why:

```
top   1 phase :  193s =  9.7% of all CPU
top  10       :  533s = 26.8%
top  50       :  945s = 47.6%
top 250       : 1613s = 81.2%
cheapest 51% of phases            = 1.1%
```

**4% of the suite is 81% of the cost.** Per-test overhead is not the problem
either: 300 empty tests run in 0.48s, so the autouse fixture stack costs 1.6 ms
a test. The mass is real work in end-to-end tests, and deleting the cheap 5,000
saves about 1%.

## 3. The bill, and where it actually goes

```
CI server job:  pip install  23s
                pytest     1134s     <- 69% of ~28 billable min/run
```

Per run: server 19m28s, ui 2m40s, native 2m44s, windows-com 1m23s (billed 2x),
relay 19s, privacy 10s. At ~4.7 runs/day that is 2,000 minutes in 15 days,
which matches the observed burn exactly.

`--durations` reports WALL time, and the distinction matters on a 4-core
runner. Measured cpu/wall:

| | cpu | wall | ratio |
|---|---|---|---|
| the two catalogue files | 273.8s | 277.9s | **0.99** |
| two guider e2e files | 32.3s | 87.6s | 0.37 |

The guider tests look expensive and mostly sleep. The catalogue tests are pure
CPU and were the real bill.

## 4. What was fixed

`search_catalog` is a linear scan of all 13,370 rows: **6.9 ms per call**,
measured. Three tests walked the whole catalogue calling it, and one could not
fail unless another already had:

```
test_catalog.py        o.id in _ids(o.id)              <- subsumed
test_catalog.py        o.id in _ids(unspaced(o.id))
test_catalog_stars.py  _ids(o.id)[0] == o.id           <- implies the first
```

Merged to one walk of two searches, both strengthened from "present" to
"FIRST" (checked against all 13,370 first — zero exceptions). **273.8s ->
181.5s.** Both claims verified still caught by sabotage: dropping `rank` from
the sort makes the star Algieba bury M1; breaking `squash_designation` empties
the unspaced lookup.

**The search index was considered and rejected on the measurement.** 6.9 ms
behind a 250 ms debounce is invisible, and a suffix array over 13,370 names
costs 1-2s of import on the Pi this ships to. Optimising product code purely to
speed a test is the wrong direction. The remaining 184s is the honest price of
"every id, both spellings, first" — a decision about whether that claim is
worth ~16% of the server job, not an inefficiency.

## 5. Why "under 1000" cannot be reached the obvious way

Per-test coverage contexts over the whole suite say:

```
strictly subset-redundant : 3,993 tests (63.7%)
greedy cover              : 1,425 tests touch every line the suite touches
```

1,425 is temptingly near the target. It is a mirage. What that cut deletes:

| guard | cut |
|---|---|
| #219 autofocus wing fragmentation | **all 19** |
| `test_mutation_sampling` — the fidelity net itself | **all 12** |
| the 2026-08-17 sweep-span work | 20 of 25 |
| focuser move contract | 23 of 32 |
| RBAC enforcement | 21 of 72 |
| #203 solar-parallax site leak | 13 of 19 |

Every end-to-end engine test covers the same ~3,200 lines while asserting
completely different things, so coverage calls them interchangeable. It cannot
see assertions. A cut chosen this way keeps whichever test touched a line first
and bins the one that catches the bug.

## 6. What is left, ranked

1. **`test_tonight_route.py::test_tonight_ranks_and_tags`** — 54s, one test.
2. **The e2e tail** — top 50 phases are half the bill; the guider/engine/rotator
   tests at 14-35s each are largely wall-clock, so they cost workers rather than
   cores. Worth checking which still burn real dwell.
3. **CI shape, not suite shape.** `-n 12` on a 4-core runner is 3x
   oversubscribed. Only 9 tests carry the `slow` marker, so the fast lane the
   config describes does not exist yet. Full suite on main, changed-area subset
   on PRs, is the lever that actually moves the bill.
4. **The catalogue sweep's 184s** — a product decision: is "every id, both
   spellings, first" worth ~16% of every CI run? Sampling it would be the first
   real fidelity trade on this list.
