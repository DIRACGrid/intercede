# IC-ADR-\[NUMBER\]: [Title of Decision]

## Metadata

- **Created By:** [Name]
- **Date:** [YYYY-MM-DD]
- **Status:** [Draft | Accepted | Rejected | Deprecated by IC-ADR-YYY | Supersedes IC-ADR-XXX]
- **Decision Maker(s):** [Name(s)]
- **Stakeholders:** [Name(s) / Role(s). Only used for decisions that affect a subset of communities]
- **Depends on:** [IC-ADR-YYY. Optional; upstream ADRs this decision builds on]

> **Scope, and how to read this.** [Optional, but recommended for long ADRs. Two to four sentences: what kind of decision this is (direction-setting or detailed), what is deferred and to where, and which sections a reviewer has to read against which ones they can skim.]

## Abstract

A short summary of the decision and why it matters, around 200 words. Write one plain sentence per decision rather than one dense paragraph. Many stakeholders will read only this section.

## Motivation

Start from what the system has to do, not from what the current code does. Three subsections work well:

- **What we have to support.** A numbered list of concrete use cases, each one something DIRACGrid does today or is committed to doing. Name real sites, real backends and real constraints. A reader who has never seen the current code should be able to judge the rest of the ADR from this list alone.
- **What the use cases require.** The functional and non-functional drivers, each tied back to the use cases it comes from.
- **Where the current code stands.** Measured against those requirements, ideally as a table. Keep it factual and say which version you checked, so a claim that goes stale is a dated citation rather than a wrong statement. Say what already works and is being kept, not only what is missing.

## Specification

Describe the chosen solution concretely: APIs, interfaces, configuration, behaviour. This is the "what we are building" section.

## Rationale

Explain *why* the chosen design looks the way it does. Why these trade-offs? Why this level of abstraction? Connect specific design choices back to the drivers in Motivation.

## Evolution

Optional. How the decision copes with future change: what can be added without breaking anything, and what would require a new ADR that supersedes this one.

## Rejected Ideas

Why were the other options set aside? This is not the same as a list of pros and cons. It is the story of what tipped the balance. Include ideas that came up in discussion but were never promoted to full options, and say why.

## Open Issues

Anything still being decided or discussed. Remove this section once the status moves to Accepted.
