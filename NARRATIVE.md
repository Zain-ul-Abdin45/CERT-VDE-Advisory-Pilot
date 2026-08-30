# How this pilot actually unfolded

*A narrative account, not a status table — what the goal was, what got looked at, what turned up, and
where each finding changed the plan. Written 19 August 2026, covering the work from Week 0 reading
through the first real matching numbers. Companion to [STATUS.md](STATUS.md) (current state) and [FAILURE_LOG.md](FAILURE_LOG.md)
(the findings themselves, in isolation).*

---

## The starting goal

The aim was never to build SRAG. It was to build a small, honest pilot on real CERT@VDE advisory data,
weighted toward the one thing the project's own text names as its hardest problem — matching
inconsistently-named products between a security advisory and an operator's asset inventory — and to
document precisely where that breaks. The output was meant to be a one-page memo to Prof. Trsek proposing
a Research Project, backed by measured evidence rather than a generic "I'm interested in AI/RAG" pitch.

## Reading first, building later

Six papers got read closely enough to be checked against their source PDFs rather than trusted from
memory or a candidate-reading summary. That carefulness paid off once: a quote about product-identifier
mismatches (the "S7-1511 vs. Sematic S7-1500 Family" line) had been attributed to the wrong paper in an
earlier candidate-reading list. Re-reading both papers in full turned up that the quote actually belonged
to a different study — a practitioner survey on CSAF adoption — not the LLM-reliability paper it had been
filed under. Small thing, but exactly the kind of error that would have been awkward to repeat to Trsek.

Alongside the reading, a diagram got sketched out — a mental model for how the pilot's matching pipeline
should work, borrowing pieces from one of the read papers (Gebauer et al.'s five-step risk-assessment
concept) plus a specific classifier choice (SVM/LDA/XGBoost) that seemed plausible at the time. Checking
that diagram against the actual paper found the classifier choice wasn't there at all — the paper
explicitly states no concrete model had been selected, and was reviewing attack graphs and Bayesian
networks instead. That correction mattered: training any classifier needs labeled data that doesn't
exist yet for this problem, so the diagram got redrawn around a rule-based cascade instead
(exact → fuzzy → version-range → part-number) — deterministic, no training data required, and a direct
extension of an alias-gate already built and working in an unrelated CI pipeline project.

## Fetching, then actually looking

Eighteen real CERT@VDE advisories got pulled, along with roughly seventy-five NVD CVE records they
reference. The original plan expected three formats per advisory — CSAF JSON, HTML, and PDF. Looking at
what actually came back, every single advisory had CSAF and HTML; none had PDF. CERT@VDE, it turned out,
doesn't publish PDF advisories anymore, at least not currently. That's a small thing that reshaped a real
piece of the plan: the three-way format-ingestion comparison scoped for later weeks became a two-way one,
and the PDF path got documented as understood-but-not-built rather than quietly dropped.

Rather than writing any parsing code against that data, the advisories got hand-segmented first — read
directly, structure by structure, the way the plan called for before any parser gets written. Two
advisories in particular, one from Weidmueller and one from ifm, showed real structural variance:
different note formats, different ways of expressing version ranges, a relationship layer in ifm's
advisory (firmware installed on hardware) that Weidmueller's simpler advisory didn't have at all. Ordinary
enough — exactly the kind of variance the plan expected to find.

## The finding that changed the whole shape of the evidence

While segmenting ifm's advisory, one specific CVE got looked up directly in NVD to see how the two sources
described the same vulnerability. ifm's advisory named the product as its own CR3171 hardware. NVD's own
record for that exact CVE named the affected vendor as CODESYS — a completely different company, the
maker of a runtime component embedded inside ifm's firmware. No shared identifier between the two records
at all; the only bridge was a sentence of prose inside ifm's advisory mentioning CODESYS by name.

That one case could have been a fluke, so it got checked systematically: every CVE-to-advisory pair
across the whole eighteen-advisory corpus, comparing the advisory's own vendor against NVD's listed
vendor for the same CVE. The ifm/CODESYS case turned out to be the *typical* case, not an exception —
fifty-eight percent of the seventy-two checked pairs showed a genuine cross-vendor mismatch, the same
embedded-component pattern repeating across completely different vendors: Mettler-Toledo's advisory was
entirely Microsoft CVEs (their device runs Windows), Balluff's spanned Grafana, OpenSSL, and half a dozen
other components, and one pair (Helmholz and MB connect line) turned out to be an OEM relationship — two
different CERT@VDE advisories citing the identical CVE.

That single measured number reshaped several other documents. The debrief's illustrative worked example
(the SIMATIC-Controller-X-vs-Ctrl-X naming mismatch) had been the strongest piece of evidence available
until this point — a good explanation of the mechanism, but constructed, not measured. Fifty-eight
percent from real data outranked it, so the literature log's open-question paragraph, the debrief's core
technical-problem section, and the debrief's list of candidates for "what's genuinely harder in this
domain" all got revised to lead with the measured number instead of the constructed one.

## A timeline correction, mid-stream

Partway through, the plan's own timeline needed correcting. The original roadmap had assumed the memo
would go out late September to early October. Exam obligations had eaten into the original July start,
which raised the question of whether the pace could even hold — and separately, a decision got made to
approach Trsek directly rather than going through Dr. Benndorf first (she's occupied with her own
Fraunhofer work now), with a fallback of asking Trsek for a Benndorf introduction later if needed. Working
backward from the semester start in October and the fact that December is effectively a write-off for
progress, early September became the actual target — about two weeks out at the point this got decided,
not the four-to-six weeks the original roadmap had assumed. That compressed the remaining plan: skip the
full FastAPI/Postgres productization, get real numbers from lean scripts against the JSON already on
disk, and prioritize the matching cascade specifically since it was the highest-leverage piece left.

## Building the cascade, and what broke along the way

Before writing new matching code, it was worth checking whether an existing alias-gate — built earlier
for an unrelated competitor-intelligence pipeline — could be reused directly, since that gate had been
cited repeatedly as evidence of already having solved "the same class of problem." Reading the actual
implementation (not just the README describing it) showed it wasn't fuzzy matching at all: it was a
substring check against a manually curated list of aliases per competitor, configured by hand in YAML.
That's a materially different technique from what SRAG's setting needs — an operator's asset-naming
can't be pre-enumerated the way a small set of known competitors can — so the new cascade got built with
real approximate string matching (rapidfuzz) instead of a straight port of the old code, and that
distinction became something worth stating precisely rather than glossing over.

A synthetic asset inventory got built next — thirty-two entries, deliberately messy, constructed from
real vendor and product data pulled out of the eighteen advisories rather than invented from a blank
page. Building the CSAF-parsing code that flattens a product tree into matchable entries turned up a real
bug on the first pass: a bare hardware-only leaf entry (no version branches under it) was silently getting
dropped by the tree-walking logic. Spot-checking the output against the raw JSON caught it before it fed
into any measurement.

Running the first version of the matching cascade produced a recall of 0.600 — noticeably worse than
expected for a cascade that had exact, fuzzy, version-range, and part-number stages all working. Digging
into which assets were being missed rather than accepting the number at face value turned up two separate,
real problems: the fuzzy-matching metric in use (`token_sort_ratio`) was too strict about word spacing
and ordering to catch genuine variants like "VariTron 300" against the real "variTRON300," and — separately
— the cascade's stage ordering let a mediocre-but-passing fuzzy score win before a much higher-confidence
part-number match ever got the chance to run. Switching the fuzzy metric to `rapidfuzz`'s `WRatio` fixed
the first problem, but introduced a new one: `WRatio` occasionally scored a short, unrelated query as a
spurious high match against an unrelated but much longer product string, purely from character-level
overlap. Adding a `token_set_ratio` floor as a guard against that specific failure mode — verified
against the actual spurious case (35.6 versus 78–100 for every genuine match) rather than tuned blindly —
closed that gap. Reordering the cascade to check part-number before falling back to fuzzy fixed the
second problem.

The result, after both fixes: precision 0.900, recall 0.900 on identity matching across the synthetic
inventory. Two of the deliberately-constructed near-miss test cases — a fictional "PROCON-WEB SCADA XT"
and a fictional "MBS UGW-Y-Series" — both produced real false positives, live demonstrations of the exact
danger the debrief's illustrative example had only argued abstractly. A separate, lower number — 33%
accuracy on version-range applicability — turned up while checking those results too closely to ignore:
in advisories that model firmware installed on hardware, the actual CVE-applicability data lives only on
the synthetic combined entries, not on the bare component entries that name-based matching naturally
favors. That's a distinct difficulty from identity matching itself, and it got written up as its own
finding rather than folded quietly into the headline number or chased down further given the time left
before the memo.

## Where things stand now

Everything above is committed to the pilot's git history and pushed to the SRAG-pilot GitHub repository
(after an unrelated hiccup — no configured default pull strategy on this machine caused two failed push
attempts before the actual fix, setting `--no-rebase` explicitly, went through). The matching cascade and
its numbers are done. What's not yet built: the CSAF/HTML ingestion parsers themselves, any structured
store, and the grounded-answering-plus-abstention slice that's meant to reuse the existing RAG project's
retrieval pattern. The target for sending the actual memo to Trsek is early September — roughly two weeks
out from where this narrative leaves off.
