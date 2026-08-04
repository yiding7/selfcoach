# selfcoach — Coach, Nutritionist & Health Advisor in One

> A **model-agnostic, zero-install** personal health toolkit.
> Sync training data → a deterministic engine computes same-muscle-group comparisons
> and your next-session prescription → generates self-contained weekly/monthly/yearly
> reports → any model you plug in narrates them in a coach's voice.

**中文** → [README.md](README.md)

---

## What makes it different

**Swap the model, keep the numbers.** Every metric, comparison, and chart is
produced by deterministic Python. The model only phrases the conclusions — it never
does arithmetic. Use Claude today, GPT tomorrow, a local Ollama the day after:
tonnage, estimated 1RM, and comparison verdicts stay **identical**.

That's the whole point, not an implementation detail. A health log is only valuable
if it's comparable over time. If the numbers came from a model, changing models
would break your own history.

**Zero dependencies.** Python standard library only. No `pip install`,
no matplotlib. Clone and run.

**Zero external references.** Reports embed charts as inline SVG in a single HTML
file. Readable offline, readable in ten years, Cmd+P straight to PDF,
light/dark follows your system.

**Works with or without Xunji (训记).** With API keys it syncs automatically;
without them, log in natural language — the analysis is exactly the same.

---

## Five-minute start

```bash
git clone git@github.com:yiding7/selfcoach.git
cd selfcoach
./install.sh              # creates dirs, links skills into your agent host. Downloads nothing
cp .env.example .env      # fill in Xunji keys, or skip entirely
./scripts/hc doctor       # checks environment, credentials, local data
```

Then either path:

```bash
# With Xunji
./scripts/hc sync --since 30d     # ⚠️ 30s/day rate limit — 30 days takes ~15 min
./scripts/hc report weekly

# Without Xunji
./scripts/hc log                  # type shorthand, Ctrl-D to finish
./scripts/hc report weekly
```

Then just ask your agent: "how did my training go this week?"

---

## Core features

### Same-muscle-group comparison

A single session usually mixes several muscle groups, so comparison happens at
**muscle-group granularity**, not session-to-session.

```
$ hc compare --group back

"Back" this time vs last
  Basis: last back session was 2026-06-28 (7 days ago, 18 sets vs 17 this time)

  Working sets   18 → 17 ↓ -5.6%
  Total volume   5800 → 6044 kg ↑ +4.2%
  Top set load   40.0 → 50.0 kg ↑ +25.0%   (shared movements only)
```

**When the two sessions share no movements, the tool refuses to compare peak loads**
and explains why — a 40 kg machine press and a 51 kg push-up equivalent are not the
same thing, and comparing them yields a "strength dropped 20%" verdict that is both
wrong and demoralizing.

### Next-session prescription

Double progression (add reps first, then load) with guardrails:

- Weekly volume over the recoverable ceiling → hold, don't add
- **In an active weight-loss phase, maintaining strength counts as success** —
  the tool holds load instead of nagging you to add weight
- Assisted machines: progress means *reducing* the assistance, not adding weight

### Reports

`hc report weekly | monthly | yearly` produces two files:

- `2026-W28.html` — the complete, self-contained report
- `2026-W28.facts.json` — computed facts, for a model to narrate

**The report is complete without any model.** Data, charts, comparisons, and
prescriptions are all there — only the coach's prose is missing.

---

## Without Xunji

This is a **first-class path**, not a fallback. Tell your assistant what you did;
it converts to shorthand for you to confirm, then persists it.

| Syntax | Meaning |
|---|---|
| `60x10` | 60 kg × 10 reps |
| `22.5x12x3` | 22.5 kg × 12 reps, 3 sets |
| `~40x10` | warm-up set, excluded from working volume |
| `62.5x8@8` | RPE 8 |
| `BWx12` / `BW+10x8` | bodyweight / bodyweight + 10 kg |
| `T:60s x3` | 60-second hold, 3 sets |
| `L:20x10 R:22.5x10` | unilateral, left and right logged separately |

---

## About sync time (important)

**The Xunji training endpoint queries one day at a time, rate-limited to 30 seconds.**
This is not an optimizable constant.

| Range | First run |
|---|---|
| 30 days | ~15 min |
| 90 days | ~45 min |
| 1 year | ~3 hours |

So by design: dates already fetched are **never re-fetched** (empty days are recorded
too), raw responses are persisted so `hc rebuild` re-derives everything offline with
zero requests, Ctrl-C is always safe, and `--budget-minutes 20` lets cron backfill
history in batches.

---

## Privacy

- The four Xunji API keys live only in `.env` (gitignored). No script, log, or report
  ever prints them.
- `data/` (training, weight, meals, personal profile) is **excluded from version
  control** by default.
- When the optional LLM adapter runs unattended, **only aggregated findings are sent** —
  raw meals, medications, and lab values never leave the machine.

---

## Design rules (enforced by tests)

1. Every **weakness** finding must link to an **actionable** finding with a number.
2. Every **strength** finding must be backed by metrics — praise is earned by data.
3. Report HTML must contain **no external references**.
4. When RPE coverage is under 30%, **all intensity-dependent conclusions are suppressed**
   rather than guessed.
5. A missing optional capability is **not an error**. Non-zero exit codes are reserved
   for real failures.

---

## Disclaimer

This tool provides general fitness and nutrition information. It is **not medical
advice**, does not diagnose or treat any condition, and is not a substitute for a
physician. Consult a qualified professional about medication, medical history, or
concerning symptoms.

Not affiliated with the Xunji app. API keys are your own. Volume landmarks are
third-party population statistics, not personal prescriptions.

## License

MIT
