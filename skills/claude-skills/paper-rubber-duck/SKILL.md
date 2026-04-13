---
name: "paper-rubber-duck"
description: "Dialectical answer engine. Thesis → antithesis → synthesis across three models. For complex written questions. Usage: /paper-rubber-duck <question>"
user-invocable: true
---

# Paper Rubber Duck (/paper-rubber-duck)

A three-model dialectical process for producing rigorous written answers to complex questions.

## When to use

Invoke when a question is:
- Genuinely debatable (multiple defensible positions exist)
- High-stakes (the answer will inform a real decision)
- Complex enough that a single-pass answer risks blind spots

Do NOT use for factual lookups, how-to questions, or anything with a clear canonical answer.

## Arguments

The user message after `/paper-rubber-duck` is the question or topic to analyze.

Examples:
- `/paper-rubber-duck Should I migrate from REST to GraphQL for the internal API?`
- `/paper-rubber-duck Is it worth switching from PostgreSQL to DuckDB for our analytics workload?`
- `/paper-rubber-duck What's the best long-term architecture for the notification system?`

## Model Assignment

| Role | Default Model | Rationale |
|------|--------------|-----------|
| **Thesis** (Model A) | `claude-sonnet-4` | Fast, capable, establishes the position |
| **Antithesis** (Model B) | `gpt-5.2` | Different training lineage = genuine diversity |
| **Synthesis** (Model C) | `claude-opus-4.6` | Strongest reasoning for judgment |

These defaults maximize reasoning diversity. The key constraint: **Models A and B must come from different model families** to avoid correlated blind spots.

## Steps

### Step 1: Thesis

Launch a **background** `general-purpose` agent with Model A.

Prompt template:

```
You are the THESIS advocate in a dialectical process.

QUESTION: {user_question}

Develop a complete, well-structured answer. Take a clear position.
Your answer should include:
1. A one-sentence thesis statement
2. Supporting arguments with evidence
3. Concrete recommendations or conclusions
4. Acknowledged limitations of your position

Write 300-600 words. Be direct and opinionated — hedging defeats the purpose.
Do NOT try to present "both sides." That's the next agent's job.
```

### Step 2: Antithesis

After Step 1 completes, launch a **background** `general-purpose` agent with Model B.

Prompt template:

```
You are the ANTITHESIS advocate in a dialectical process.

QUESTION: {user_question}

THE THESIS (another model's answer):
---
{thesis_text}
---

Your job is to challenge this thesis. You may:
- Directly refute its claims
- Propose a fundamentally different approach
- Identify unstated assumptions or overlooked evidence
- Argue the thesis is correct but for the wrong reasons

Develop a counter-position. Include:
1. A one-sentence counter-thesis
2. Specific weaknesses in the thesis
3. Your alternative argument with evidence
4. What the thesis got right (steelman before attacking)

Write 300-600 words. Be adversarial but intellectually honest.
Do NOT just nitpick. Offer a genuinely different perspective.
```

### Step 3: Synthesis

After Step 2 completes, launch a **sync** `general-purpose` agent with Model C.

Prompt template:

```
You are the JUDGE in a dialectical process. Two models have debated a question.
Your job is to synthesize the strongest possible answer.

QUESTION: {user_question}

THESIS (Model A):
---
{thesis_text}
---

ANTITHESIS (Model B):
---
{antithesis_text}
---

Produce the final answer. You must:
1. Identify where each side is strongest and weakest
2. Resolve contradictions with evidence and reasoning
3. Produce a unified answer that is better than either input
4. Flag any points where genuine uncertainty remains

Structure your output as:

**Verdict**: One sentence — who was more right, and why.

**Synthesis**: The unified answer (400-800 words). This is the answer the user
actually reads, so make it complete and actionable.

**Dissent log**: Bullet list of points where the two models genuinely disagreed
and the evidence is ambiguous. The user should know where confidence is low.

Do NOT split the difference for diplomacy. If one side is clearly right, say so.
If both are partially right, explain exactly which parts survive.
```

### Step 4: Present

Display the synthesis to the user. Below it, include a collapsible summary:

```
───────────────────────────────────
Models: Thesis={model_a} → Antithesis={model_b} → Synthesis={model_c}

Thesis gist:  {one-line summary}
Antithesis gist: {one-line summary}
───────────────────────────────────
```

Do NOT dump the full thesis/antithesis unless the user asks. The synthesis is the deliverable.
