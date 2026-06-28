# Provenance Guard

An API for detecting AI-generated text and providing transparent attribution labels
to readers on a writing platform. Built with Flask, Groq (llama-3.3-70b-versatile),
and stylometric heuristics.

---

## How It Works: Architecture

A submitted piece of text travels through two independent detection signals, gets combined into a confidence score, maps to a transparency label, and is written to the audit log before the response is returned.

**Submission flow:**
POST /submit
     │
     ├─────────────────────┬─────────────────────┐
     │                     │                     │
     ▼                     ▼                     │
Groq LLM Signal    Stylometric Heuristics        │
(semantic, 0–1)    (variance, TTR, punct, 0–1)   │
     │                     │                     │
     └──────────┬──────────┘                     │
                │                                │
                ▼                                │
       Confidence Scoring                        │
       (60% LLM + 40% stylo)                     │
                │                                │
                ▼                                │
       Label Variant Selection  ◄────────────────┘
       (≥0.70 AI / 0.40–0.69
        uncertain / <0.40 human)
                │
                ▼
           Audit Log
                │
                ▼
         JSON Response

**Appeal Flow:**
POST /appeal
     │
     ▼
Look up content_id in audit log
     │
     ├── Not found ──► Return 404 error
     │
     ▼
Update status
"classified" → "under_review"
     │
     ▼
Append appeal reasoning + appeal timestamp to log entry
     │
     ▼
Return confirmation to creator

---

## Detection Signals

### Signal 1: Groq LLM (60% weight)
The LLM reads the text holistically and returns a probability that it was
AI-generated. It captures things such as, unnatural transitions, overuse of hedging phrases, generic sentence structures, and an overall smoothness that human writing rarely sustains.

I gave it 60% of the combined score because semantic patterns are harder to fake than structural ones. A human can vary their sentence lengths dliberately but it is much harder to purposefully write with the messiness and specificity of genuine human voice. 

**Blind spot:** heavily polished human writing (academic papers, legal briefs) and heavily edited AI output both fool it. It also has no memory of what "normal" looks like for a specific creator, so it can't flag stylistic inconsistency.

### Signal 2: Stylometric Heuristics (40% weight)
Measures three structural properties: 

- **Sentence length variance:** AI text tends to have suspiciously similar sentence lengths. Human writing is messier with short fragments, long run-ons, deliberate variation. Low variance = higher AI score.
- **Type-token ratio (TTR):** unique words ÷ total words. AI models reuse vocabulary predictably. Low TTR = higher AI score.
- **Punctuation density:** commas, semicolons, colons per word. AI tends to use punctuation more consistently and formally.

I gave stylometrics 40% because it's more gameable and more likely to misfire on legitimate edge cases.

**Blind spot:** simple or repetitive human writing looks structurally uniform and scores as AI. A children's book author or someone writing in a deliberately minimal style gets penalized unfairly.

### Combining the signals
Weighted average: `confidence = 0.6 × llm_score + 0.4 × stylo_score`

| Confidence | Attribution | Meaning |
|---|---|---|
| ≥ 0.70 | likely_ai | High confidence AI-generated |
| 0.40 – 0.69 | uncertain | System cannot confidently determine |
| < 0.40 | likely_human | High confidence human-written |

---

## Confidence Scoring

The score is designed to reflect genuine uncertainty rather than force a binary output. A 0.95 and a 0.51 both technically cross the "AI" threshold in a binary system, as 0.95 gets the high-confidence AI label and 0.51 gets the uncertain label, because those are meaningfully different levels of evidence.

The threshold asymmetry is intentional: the spec notes that a false positive, flagging a human's writing as AI, is worse than a false negative on a writing platform. Requiring 0.70 to reach "likely_ai", rather than 0.50, means the system has to be fairly confident before making an accusation.

**Example submissions from Milestone 4 testing:**
High-confidence case (clearly AI-generated text):
```json
{
  "text": "Artificial intelligence represents a transformative paradigm shift...",
  "attribution": "likely_ai",
  "confidence": 0.8,
  "llm_score": 0.8,
  "stylo_score": 0.201
}
```

Lower-confidence case (casual human writing):
```json
{
  "text": "ok so i finally tried that new ramen place downtown and honestly?...",
  "attribution": "likely_human",
  "confidence": 0.161,
  "llm_score": 0.2,
  "stylo_score": 0.103
}
```

The scores vary meaningfully (0.8 vs 0.161) and both signals agree in these clear
cases. In borderline cases (formal human writing scored 0.472) the signals diverge,
which is exactly when the "uncertain" label is most appropriate.

If I were deploying this for real, I would replace the fixed 60/40 weighting with
a learned weighting calibrated on a labeled dataset, and I would add a minimum
text length check — stylometrics below ~50 words produce unreliable variance scores.

---

## Transparency Labels
All three variants are shown to users in plain language. The label returned by
`POST /submit` changes based on the confidence score.

**High-confidence AI (confidence ≥ 0.70):**
> "Our system has determined with high confidence that this content was likely
> AI-generated. This assessment is based on writing style and structural analysis.
> If you believe this is incorrect, you may submit an appeal."

**Uncertain (confidence 0.40 – 0.69):**
> "Our system could not confidently determine whether this content was written by
> a human or generated by AI. It has been marked for review. If you are the
> creator, you may submit an appeal to provide additional context."

**High-confidence human (confidence < 0.40):**
> "Our system has determined with high confidence that this content was likely
> written by a human. No further action is required."

---

## Appeals Workflow

Any creator can contest a classification using the `content_id` returned by
`POST /submit`.

**What the creator provides:** their `content_id` and written reasoning explaining
why they believe the classification is incorrect.

**What the system does:**
1. Looks up the `content_id` in the audit log
2. Updates the entry's status from `"classified"` to `"under_review"`
3. Appends the creator's reasoning and an appeal timestamp to the log entry
4. Returns a confirmation to the creator

**What a human reviewer would see:** the original text, both individual signal
scores, the combined confidence score, the label that was shown to the reader,
and the creator's reasoning — all in a single log entry.

Automated re-classification is not implemented. The appeal creates a paper trail
for a human to review.

---

## Rate Limiting 

**Limits:** 10 requests per minute, 100 requests per day per IP address.

**Reasoning:** A real writer submitting their own work might submit a handful of
pieces in a session — 10 per minute is generous for legitimate use and still
blocks a script flooding the endpoint. 100 per day reflects realistic daily
volume for even a prolific creator. An adversary trying to probe the classifier
with thousands of inputs to find patterns would hit the daily limit quickly.

**Evidence — rate limit test output (12 rapid requests):**
200
200
200
200
200
200
200
200
429
429
429
429

The endpoint returns HTTP 429 after the per-minute limit is exceeded.

---

## Audit Log

Every attribution decision is written to `audit_log.json`. The log captures timestamp, content ID, creator ID, attribution result, confidence score, both individual signal scores, status, and any appeal information.

Sample entries (from `GET /log`):

```json
{
  "attribution": "likely_ai",
  "confidence": 0.8,
  "content_id": "7522d08c-4698-49ea-a368-bb7516aafa69",
  "creator_id": "test-user-1",
  "llm_score": 0.8,
  "status": "classified",
  "timestamp": "2026-06-27T01:15:25.202631Z"
},
{
  "attribution": "likely_human",
  "confidence": 0.161,
  "content_id": "d41e618e-2048-4765-b8ea-04bc97f54489",
  "creator_id": "test-2",
  "llm_score": 0.2,
  "status": "classified",
  "stylo_score": 0.103,
  "timestamp": "2026-06-27T01:20:21.345901Z"
},
{
  "appeal_reasoning": "I wrote this myself from personal experience. I am a
    non-native English speaker and my writing style may appear more formal
    than typical.",
  "appeal_timestamp": "2026-06-27T01:29:02.799758Z",
  "attribution": "uncertain",
  "confidence": 0.546,
  "content_id": "27722cb4-52d9-43ec-af44-0e31d9abc741",
  "creator_id": "test-user-1",
  "llm_score": 0.8,
  "status": "under_review",
  "stylo_score": 0.166,
  "timestamp": "2026-06-27T01:28:12.195631Z"
}
```

---
## Known Limitations

**Non-native English speakers writing formally:** this is the system's most consequential failure mode. A careful, grammatically precise writer with limited vocabulary range will score high on stylometrics, such as low TTR, low sentence length variance, because those are exactly the properties that distinguish AI writing from casual human writing. But they're also properties of careful, deliberate human writing in a second language. The LLM signal may also penalize formal phrasing that reads as template-like. Both signals push in the wrong direction for the same input, and the combined score could cross 0.70 and produce a high-confidence AI label for genuinely human work. This is precisely why the appeals workflow exists and why the threshold is set at 0.70 rather than 0.50.

**Very short text:** stylometric variance is statistically unreliable with fewer than 3–4 sentences. A haiku or a single-sentence submission gives the variance calculation almost nothing to work with, and the score defaults to 0.5 (neutral), effectively handing all the weight to the LLM signal. In production I would flag submissions under ~50 words as "insufficient data" and return a forced uncertain label rather than pretending the score is meaningful.

---

## Spec Reflection

**One way the spec helped:** the hint about false positives being worse than false negatives directly shaped my threshold design. I initially had the "likely_ai" threshold at 0.5, which would make the system equally trigger-happy in both directions. The spec pushed me to think about the asymmetry, a writer wrongly accused of using AI loses trust in the platform is much worse than AI content seeming too human. Setting the threshold at 0.70 encodes that asymmetry in the scoring itself.

**One way my implementation diverged:** my planning.md specified that the two signals would run in parallel. In practice they run sequentially, the Groq API call happens first, then stylometrics. For a production system with latency requirements I would parallelize them using threading or async, but for this scope the sequential approach is simpler and the latency difference is negligible.

---

## AI Usage

**Instance 1 — Flask app skeleton and Groq signal function:**
I provided my detection signals spec section and architecture diagram and asked
for a Flask app skeleton with a `POST /submit` stub and a Groq signal function.
The AI produced a working skeleton, but the Groq prompt it wrote asked the model
to return a simple yes/no rather than a probability score. I revised the prompt
to explicitly request a JSON object with an `ai_probability` float, which matched
my spec's requirement for a continuous 0–1 output rather than a binary flag.

**Instance 2 — Stylometric heuristics function:**
I provided the uncertainty representation section of my spec and asked for a
stylometric function computing sentence length variance, TTR, and punctuation
density. The AI produced the function correctly but combined the three metrics
with equal weighting and no normalization, meaning punctuation density (a small
raw number) was being drowned out by variance (a potentially large number). I
added normalization (`min(punct_density * 5, 1.0)`) and verified the function
produced meaningful output on the four test inputs before wiring it in.

---

## API Reference

### POST /submit
Accepts a piece of text for attribution analysis.

**Request:**
```json
{
  "text": "your content here",
  "creator_id": "user-identifier"
}
```

**Response:**
```json
{
  "content_id": "uuid",
  "attribution": "likely_ai | uncertain | likely_human",
  "confidence": 0.0,
  "label": "transparency label text"
}
```

### POST /appeal
Contest a classification.

**Request:**
```json
{
  "content_id": "uuid-from-submit",
  "creator_reasoning": "explanation"
}
```

### GET /log
Returns all audit log entries as JSON.

---

## Setup

```bash
git clone <your-repo>
cd ai201-project4-provenance-guard
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
# create .env with GROQ_API_KEY=your_key
python app.py
```