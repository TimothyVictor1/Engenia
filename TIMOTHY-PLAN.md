# Engenia: Timothy's Plan

**Your role: integrator and owner of the spine.**
You build the core that everything plugs into, and you are the only person who merges work into `main`.

Your prompts are in `TIMOTHY-PROMPTS.md`. Shared rules and setup are in `PROJECT.md`.

If the night starts late, shift every time below by the same amount.

---

## Your four agents

Think of each agent as one worker with one job. You give each worker a prompt, then check on them.

| Name | Agent | Its job in one line |
|---|---|---|
| T1 | Claude A | The engine: reads requirements, decides what runs, runs things in parallel, remembers results |
| T2 | Claude B | The brain wiring: the code that lets AI models act as agents, plus the checking gate |
| T3 | Codex | The hull: turns numbers into a 3D model, then checks all parts fit together |
| T4 | OpenCode | The paperwork: example requirements, harsh test scenarios, physical test steps |

---

## What you must finish, in order

### Step 0, 17:45 to 18:30: set the rules of the game

| Do this | With | Done when |
|---|---|---|
| Create the GitHub repository `engenia`, add Person 1 and Person 2, protect `main` | You, by hand | Both teammates can clone it |
| Paste the **Step 0** prompt | T1 | A pull request is open |
| Review it with both teammates, then merge | You | `uv run engenia run harbour-listener` passes with placeholder stages |

Nobody else can start until this is merged. It is the most important 45 minutes of the night.

### Sprint 1, 18:30 to 21:00: four workers, four workspaces

Start all four at once. Each gets its own Superset workspace.

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| T1-S1 | T1 | Loads requirements, checks their quality, works out the order of stages, caches results | Running twice does nothing the second time; changing one requirement reruns only the right stages |
| T2-S1 | T2 | The agent loop (model calls, three allowed tools, attempt limits) and the intake agent | A fake model can be driven through a fail-then-pass loop in tests |
| T3-S1 | T3 | A parametric buoy hull that exports STEP and STL and reports its mass and volume | Two builds give identical files |
| T4-S1 | T4 | Twelve example requirements and four harsh scenarios | `engenia lint harbour-listener` passes |

### Merge 1, 21:00 to 21:30

You merge everyone's sprint 1 pull requests, one at a time, in this order:

1. B1-S1 (Person 2, infrastructure)
2. T1-S1 (core)
3. A2-S1 (Person 1, check scripts)
4. T3-S1 (hull)
5. A1-S1 (Person 1, electronics)
6. T2-S1 (harness)
7. T4-S1 (requirements and scenarios)

After each merge, tell everyone to rebase. Person 2 then runs the exit test: a real hull is generated, measured, and rebuilt with identical files.

### Sprint 2, 21:30 to 23:30

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| T1-S2 | T1 | Stages run in parallel; the contract board accepts or rejects change requests | One failing stage does not stop the others |
| T2-S2 | T2 | Architect agent, verification agent, and the gate with its five checks | Verification agent cannot see the design; a tampered check file fails the gate |
| T3-S2 | T3 | Integration check: board fits bay, mass adds up, pins match | One pass and one fail test per check |
| T4-S2 | T4 | Physical test plan, one test per requirement | Twelve requirements give twelve tests |

### Merge 2, 23:30 to 00:30

Merge order: T1-S2, A2-S2, T2-S2, A1-S2, T3-S2, B1-S2, T4-S2.

Exit test, run by Person 2: three design agents run in parallel, a forced failure in one leaves the other two finished, and an accepted change request reruns only the affected stages.

### Sprint 3, 00:30 to 03:00

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| T1-S3 | T1 | A run that was killed can resume; `engenia plan` explains what will run and why | Killed run finishes after `--resume` |
| T2-S3 | T2 | Final audit; refuses to run if design and verification use the same model family; AI proposals replace the fixed rules | Final stage fails when any requirement has no result |
| T3-S3 | T3 | A second shape (box-style sensor housing) through the same mechanical stage | Hull tests still pass unchanged |
| T4-S3 | T4 | A second project, `deck-sensor` | It runs and passes the gate |

### Merge 3, 03:00 to 03:30, then proof until 06:00

Merge order: T1-S3, A2-S3, T3-S3, A1-S3, B1-S3, T2-S3, T4-S3.

Person 2 runs the proof commands. You fill in the "done" tick list and the "known slots" table in `PROJECT.md`.

---

## Your jobs that no agent does

- **Merging.** Only you press merge. Only when CI is green, the review says approve, the files are inside that seat's folders, and the acceptance list is ticked.
- **Contract changes.** Only you change files in `engenia/contracts/`. If a teammate needs a change, you make it on `main` and tell everyone to rebase.
- **Cuts.** If something is blocked for 30 minutes, you decide: take it over by hand, or reduce it to a placeholder that keeps its contract.
- **Review requests.** Send each pull request for review with `Review PR #<number>, task <task ID>`.

---

## What you depend on, and who depends on you

| You need | From | By |
|---|---|---|
| Working CI and containers | Person 2 | 20:00 |
| Check scripts the gate can call | Person 1 (A2) | 21:00 |
| Electronics and firmware facts for the integration check | Person 1 (A1) | 23:30 |

| Others need from you | Who | By |
|---|---|---|
| Step 0 merged | Everyone | 18:30 |
| Hull facts (`interface.json` from mechanical) | Person 1, Person 2 | 21:00 |
| Parallel scheduler and contract board | Everyone | 23:30 |

---

## If you fall behind

Supervising four agents and merging is a lot for one person. Cut in this order:

1. T4 tasks become placeholders (keep the twelve requirements, drop the rest).
2. T3-S3 (second shape) is dropped; `deck-sensor` then cannot run and T4-S3 is dropped too.
3. T2-S3 part 3 (AI proposals) is dropped; the fixed rules stay.

Never cut: Step 0, T1-S1, T1-S2, T2-S2. Those are the architecture.
