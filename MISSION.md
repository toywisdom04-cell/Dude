# DUDE — Mission, Loop, and Engineering Rule (permanent reference)

## Mission statement

> **DUDE is an autonomous personal computer coworker. DUDE understands the
> user's goals and ongoing work, observes applications and the environment,
> uses and discovers tools, performs tasks across software, verifies
> outcomes, learns from successful and failed attempts, remembers useful
> knowledge and procedures, improves its future execution, explains results,
> and proactively suggests better ways to accomplish work. Language models
> are interchangeable cognitive engines inside DUDE; they are not DUDE
> itself.**

## Permanent engineering rule

> **Never build a feature merely because it makes DUDE answer better. Build
> it if it makes DUDE understand, act, learn, remember, verify, improve, or
> collaborate better.**

Deepest principle: DUDE should become better at doing the user's work, not
merely better at talking about it.

## Canonical cognitive loop

```text
                         ┌───────────────────────┐
                         │         DUDE          │
                         │   Persistent Agent    │
                         └───────────┬───────────┘
                                     │
              ┌──────────────────────┼───────────────────────┐
              │                      │                       │
          UNDERSTAND              OBSERVE                REMEMBER
              │                      │                       │
          intent                 screen/UIA             episodic
          purpose                applications           semantic
          goals                  audio/environment      procedural
          constraints            user activity          user/work
              │                      │                       │
              └──────────────────────┼───────────────────────┘
                                     │
                                THINK / PLAN
                                     │
                         ┌───────────┴───────────┐
                         │                       │
                    KNOWN PATH              UNKNOWN PATH
                         │                       │
                  retrieve skill           explore / learn
                  retrieve procedure       inspect app
                  adapt parameters         discover controls
                         │                       │
                         └───────────┬───────────┘
                                     │
                                  EXECUTE
                                     │
                         UIA / APIs / tools / GUI
                                     │
                                  VERIFY
                                     │
                         ┌───────────┴───────────┐
                         │                       │
                      SUCCESS                 FAILURE
                         │                       │
                    remember               diagnose
                    generalize              recover
                    improve                 retry/adapt
                         │                       │
                         └───────────┬───────────┘
                                     │
                                  RESULT
                                     │
                         show user + explain what
                         was done + suggestions
```

Per-turn checklist:

```text
USER GOAL
   ↓
UNDERSTAND — actual goal? purpose? constraints?
   ↓
CONTEXT — what is the user doing? which apps/data/environment?
          what relevant history do I remember?
   ↓
KNOWLEDGE — do I already know this workflow?
   ├── YES → retrieve verified procedure
   ├── PARTIAL → adapt known procedure
   └── NO → inspect, explore, discover, learn
   ↓
PLAN — capabilities, applications, data, sequence
   ↓
EXECUTE — UIA / APIs / tools / GUI / applications
   ↓
VERIFY — did the intended outcome actually happen?
   ├── SUCCESS → remember, generalize, improve procedure
   └── FAILURE → diagnose → recover / another approach → retry → verify
   ↓
RESULT — show what happened, explain, preserve context, suggest next actions
```

## Work memory (not a screenshot warehouse)

DUDE remembers jobs, not screenshots:

```text
JOB: Monthly Sales Report
PURPOSE: Turn raw sales data into the monthly management report.
CONTEXT: User performs this near the end of each month.
KNOWN APPLICATIONS: Excel, Browser, Email
KNOWN DATA: Sales workbook, management-report template
VERIFIED PROCEDURE:
  1. Open sales workbook
  2. Import current-month data
  3. Refresh calculations
  4. Generate required summary
  5. Update report template
  6. Verify totals
  7. Prepare email
  8. Verify attachment/report
  9. Send
KNOWN VARIATIONS: Different workbook filename each month.
VERIFICATION: Totals must match source data.
RECOVERY: If refresh fails → inspect workbook state, retry alternate path.
LAST SUCCESS: ...
CONFIDENCE / PROVENANCE: ...
```

Think: "The user is performing a repeatable workflow." Never: "I need
to save every screenshot."

## Voice is an interface layer, not the system

```text
MIC / VOICE → STT → LIVE TURN → BRAIN / PROVIDER → STREAM → TTS → USER
(barge-in, generations, fallback, streaming, tools, relation, queue)
```

lives under RESULT / conversation, alongside SCREEN/UI. Every future
phase must strengthen:

```text
Understand → Observe → Remember → Plan → Act → Verify → Recover → Learn → Improve → Collaborate
```
