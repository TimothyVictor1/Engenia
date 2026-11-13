# Engenia: Person 2's Plan

**Your role: the cloud, the simulations, and the team's tester.**
You make sure everything runs on the cloud machine, you build the virtual prototype, and you run the exit test at each merge.

Your prompts are in `PERSON2-PROMPTS.md`. Shared rules and setup are in `PROJECT.md`.

If the night starts late, shift every time below by the same amount.

---

## Your one agent

| Name | Agent | Its job in one line |
|---|---|---|
| B1 | OpenCode | First builds the cloud setup, then the wave and stress simulations |

You have one agent on purpose. Your own hands are needed for the cloud account and for testing.

---

## Before you start (17:45 to 18:30): do these by hand

Start now, in parallel with Timothy's Step 0.

1. Accept the GitHub invitation to the `engenia` repository.
2. Install the tools and Superset as in `PROJECT.md` section 7. Log in to `opencode` once in a normal terminal.
3. Create the cloud machine: Ubuntu 24.04, 8 or more cores, 32 GB memory, 100 GB disk.
4. Set a spending alert and an automatic shutdown time on the cloud account.
5. Note the machine's address and confirm you can log in with SSH.
6. Review Timothy's Step 0 pull request. Look at one thing: does the CI file run the pipeline?

---

## What you must finish, in order

### Sprint 1, 18:30 to 21:00: the cloud

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| B1-S1 | B1 | Machine setup script, four containers (CAD, electronics, firmware, simulation), CI that runs the pipeline twice and compares the results | CI is green on the cloud machine |

**Your hand steps during this sprint**

1. When the agent has written `infra/vm_bootstrap.sh`, copy it to the machine and run it.
2. Register the machine as a GitHub runner with the label `engenia-build`, following `infra/runner_setup.md`.
3. Add the four repository secrets listed in `PROJECT.md` section 7.5. Get the values from Timothy.
4. Run `containers/build.sh` on the machine.

**This is urgent.** Everyone's pull requests need CI. Aim to have CI green by 20:00, even if the containers are basic.

### Merge 1, 21:00 to 21:30: you test

Timothy merges. Then you run on the cloud machine:

```bash
git checkout main && git pull && uv sync
uv run engenia lint harbour-listener
uv run engenia run harbour-listener
uv run engenia run harbour-listener     # second time: nothing should run
```

Pass means: a real hull file exists in `projects/harbour-listener/build/mechanical/`, and the second run does no work. Post the output in the team chat.

### Sprint 2, 21:30 to 23:30: waves

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| B1-S2 | B1 | A list of simulation cases made from the requirements; a wave simulation that reports how far the buoy tilts; safety checks on every result | Doubling the wave height doubles the tilt; a broken mesh is rejected |

### Merge 2, 23:30 to 00:30: you test

Three things to prove. Timothy's T1-S2 tests show how to force each one.

1. Mechanical, electronics and firmware start at the same time.
2. Break one of them on purpose: the other two still finish.
3. Accept a change request for a taller battery bay: only electronics and mechanical rerun.

### Sprint 3, 00:30 to 03:00: stress, and a place for flow

| Task | Agent | What gets built | Done when |
|---|---|---|---|
| B1-S3 | B1 | A stress simulation of the hull under water pressure; a prepared but switched-off flow simulation | Doubling the depth doubles the stress; the flow case shows as "not run" |

### Merge 3, 03:00 to 03:30: you test

Start a run, kill it half-way, then:

```bash
uv run engenia run harbour-listener --resume
```

Pass means it finishes without redoing completed stages.

### Proof, 03:30 to 06:00

Run the six commands in the **Proof block** at the end of your prompts file. Paste every output in the team chat. Then help Timothy fill in the tick list in `PROJECT.md`.

---

## Your jobs that no agent does

- **The cloud account.** Creating the machine, the runner, the secrets, the spend limit.
- **Exit tests.** At every merge window you say pass or fail, with the output as proof.
- **Honest reporting.** If a test fails, say so plainly. A hidden failure tonight becomes a failed demo on Sunday.
- **Watching the machine.** If disk or memory fills up, CI fails for everyone. Check with `df -h` and `free -g` at each merge.

---

## What you depend on, and who depends on you

| You need | From | By |
|---|---|---|
| Step 0 merged | Timothy | 18:30 |
| Model endpoint and key for the secrets | Timothy | 19:00 |
| Hull and assembly facts for the simulations | Timothy (T3) | 23:30 |
| Scenario files (wave height, depth) | Timothy (T4) | 21:00 |

| Others need from you | Who | By |
|---|---|---|
| Green CI on the cloud machine | Everyone | 20:00 |
| Firmware container | Person 1 | 21:30 |
| One simulated result (wave tilt) | Everyone, for the pitch | 00:30 |

---

## Folders you may edit

- `containers/`, `infra/`, `.github/`
- `engenia/sim/`, `packs/buoy/sim/`
- `tests/infra/`, `tests/sim/`

Nothing else. If you need a change elsewhere, ask the owner. If you need a contract change, ask Timothy.

---

## Handing in each task

1. The agent runs the checks, commits, pushes and opens a pull request.
2. You tick the acceptance list in the prompt yourself.
3. Post the link in the team chat and ask for review: `Review PR #<number>, task <task ID>`.
4. If the review says "changes needed", paste the list into the fix prompt at the bottom of your prompts file.

---

## If you fall behind

Cut in this order:

1. B1-S3 flow template (leave a README only).
2. B1-S3 stress simulation.
3. Container pinning by digest becomes pinning by version tag, written down as a known gap.

Never cut: green CI, the exit tests, the wave simulation.
