# Engenia: Person 1's Plan

**Your role: electronics, firmware, and every check script.**
You build the two design stages for the electronic side, and you write the scripts that decide pass or fail for the whole project.

Your prompts are in `PERSON1-PROMPTS.md`. Shared rules and setup are in `PROJECT.md`.

If the night starts late, shift every time below by the same amount.

---

## Your two agents

| Name | Agent | Its job in one line |
|---|---|---|
| A1 | Codex | Designs the circuit and the device software |
| A2 | OpenCode | Writes the measuring scripts, the planted-fault tests and the final report table |

---

## Before you start (17:45 to 18:30)

1. Accept the GitHub invitation to the `engenia` repository.
2. Install the tools and Superset as in `PROJECT.md` section 7.
3. Log in to `codex` and `opencode` once in a normal terminal.
4. Read `PROJECT.md` section 6 (the contracts). Your stages must match it exactly.
5. Review Timothy's Step 0 pull request. Look at one thing: are the facts your stages need in the contract? (bay size, energy budget, pin map)

You cannot start building until Step 0 is merged.

---

## What you must finish, in order

### Sprint 1, 18:30 to 21:00: two workers, two workspaces

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| A1-S1 | A1 | A parts list with a source for every number; a stage that picks a battery, wires the circuit, and reports power and mass | 7 days picks the smallest battery, 30 days picks a bigger one; if nothing fits, it files one change request and still finishes |
| A2-S1 | A2 | Nine measuring scripts: mass, buoyancy, print-bed fit, wall thickness, stability, power, battery life, circuit rule check, bay fit | Each script has a pass test, a fail test and a missing-input test |

**A2-S1 is urgent.** Timothy's gate and hull stage call these scripts. Aim to open that pull request by 20:30.

### Merge 1, 21:00 to 21:30

Timothy merges. After each merge he announces, run in every open workspace:

```bash
git fetch origin && git rebase origin/main
```

### Sprint 2, 21:30 to 23:30

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| A1-S2 | A1 | Device software: a tone detector, its unit tests, and a replay tool that runs it on two recorded sound files | Detects at least 9 of 10 tones; at most 2 false alarms per hour on the quiet file |
| A2-S2 | A2 | Five more scripts; ten planted faults that the checks must catch; the trace matrix | 10 of 10 faults caught; matrix lists all twelve requirements |

When A1-S2 names its three new checks (`detection_rate`, `false_alarm_rate`, `fw_tests_pass`), give those names to A2 straight away. A2 writes them.

### Sprint 3, 00:30 to 03:00

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| A1-S3 | A1 | Compile for the real chip and report memory use; a placeholder for the emulator; more parts with second sources | A too-small memory budget fails the check |
| A2-S3 | A2 | Two memory checks; the assumption ledger; the benchmark runner | Three runs of both projects give identical results |

---

## Your jobs that no agent does

- **Check the parts list by eye.** Every number in `facts.toml` must have a real datasheet and page. An agent may invent values. Open two or three datasheets and confirm.
- **Own the fault suite.** If a planted fault is not caught, that is a hole in the project's main claim. Fix it before anything else.
- **Hand over names.** When A1 adds a check name, A2 must write it. You are the link.

---

## What you depend on, and who depends on you

| You need | From | By |
|---|---|---|
| Step 0 merged | Timothy | 18:30 |
| Firmware container that works | Person 2 | 21:30 |
| Hull facts (bay size, mass) | Timothy (T3) | 21:00 |

| Others need from you | Who | By |
|---|---|---|
| Nine check scripts | Timothy's gate and hull stage | 21:00 |
| Electronics facts (power, mass, board size) | Timothy's integration check, Person 2's simulation | 23:30 |
| Fault suite result | Everyone, for the pitch | 00:30 |

---

## Folders you may edit

- `engenia/agents/electronics/`, `engenia/agents/firmware/`, `packs/buoy/parts/`
- `verifiers/`, `evals/`, `engenia/evidence/`
- `tests/electronics/`, `tests/firmware/`, `tests/verifiers/`, `tests/evidence/`

Nothing else. If you need a change elsewhere, ask the owner. If you need a contract change, ask Timothy.

---

## Handing in each task

1. The agent runs the checks, commits, pushes and opens a pull request.
2. You tick the acceptance list in the prompt yourself. Do not trust "all done" without looking.
3. Post the link in the team chat and ask for review: `Review PR #<number>, task <task ID>`.
4. If the review says "changes needed", paste the list into the fix prompt at the bottom of your prompts file.

---

## If you fall behind

Cut in this order:

1. A1-S3 emulator placeholder and extra parts.
2. A2-S3 benchmark runner (Person 2 can run the pipeline three times by hand).
3. A2-S2 trace matrix becomes a plain table without the summary.

Never cut: A2-S1, the fault suite, A1-S1.
