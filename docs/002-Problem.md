```markdown
---
id: DOC-002
title: Problem Statement
version: 1.0
status: Draft
owner: CTO
last_updated: 2026-07-07
tags:
  - product
  - strategy
  - research
---

# Problem Statement

> Modern on-chain research is fragmented, inefficient, difficult to reproduce, and poorly integrated with artificial intelligence.

---

# Background

Decentralized financial markets generate enormous amounts of public data every second.

Every block contains valuable information including:

- newly deployed smart contracts
- newly created trading pairs
- liquidity events
- swap transactions
- token transfers
- wallet interactions
- protocol activity

Although all of this information is publicly accessible, transforming it into meaningful research remains a difficult engineering problem.

The challenge is no longer data availability.

The challenge is knowledge extraction.

---

# The Current Workflow

Today, quantitative researchers rarely work inside a single environment.

Instead, a typical investigation requires switching between multiple disconnected tools.

Example workflow:

DEX Screener

↓

Blockchain Explorer

↓

GeckoTerminal

↓

DEXTools

↓

Wallet Tracker

↓

Python Notebook

↓

Spreadsheet

↓

Personal Notes

↓

Manual Conclusions

Each tool answers only a small portion of the overall research question.

The researcher becomes responsible for integrating every piece of information manually.

---

# Problems

## Fragmented Data Sources

Relevant information is distributed across many APIs, dashboards, explorers, and analytics platforms.

There is no unified research workflow.

---

## Manual Investigation

Researchers repeatedly perform the same tasks:

- opening multiple websites
- copying wallet addresses
- checking liquidity
- inspecting holders
- comparing charts
- reviewing transactions

Most of these activities could be automated.

---

## Poor Reproducibility

Research results often exist only inside notebooks, browser tabs, or personal notes.

Months later it becomes difficult—or impossible—to reproduce exactly how a conclusion was reached.

This significantly reduces research quality.

---

## Weak Knowledge Retention

Valuable discoveries are rarely converted into reusable organizational knowledge.

Insights remain inside individual researchers rather than becoming permanent project assets.

The same investigations are repeated multiple times.

---

## High Time Cost

Collecting data frequently consumes more time than analyzing it.

Researchers spend significant effort preparing information before meaningful research can even begin.

---

## Limited Experimentation

Because data preparation is expensive, researchers tend to validate only a small number of hypotheses.

This reduces creativity and slows discovery.

---

## AI Cannot Fully Participate

Large language models and coding agents perform best when:

- context is structured
- data is normalized
- documentation is consistent
- relationships are explicit

Traditional blockchain research workflows provide none of these characteristics.

As a result, AI contributes only partially to the research process.

---

# Root Cause

The fundamental issue is not the lack of blockchain data.

The fundamental issue is the absence of an integrated research operating system capable of transforming raw blockchain activity into structured knowledge.

Current tools optimize for visualization.

Very few optimize for continuous research.

---

# Our Perspective

We believe blockchain research should be treated as an engineering discipline.

Instead of manually assembling information from independent tools, researchers should interact with a unified system capable of:

- continuously collecting observations
- organizing data
- generating research datasets
- computing features
- documenting experiments
- preserving knowledge
- accelerating iteration

Research should become programmable.

---

# Why Existing Tools Are Not Enough

Existing platforms provide valuable capabilities, but each solves only a narrow problem.

Examples include:

- blockchain explorers
- market dashboards
- token analytics
- wallet trackers
- charting tools

These systems primarily expose information.

They do not manage the complete lifecycle of quantitative research.

The missing layer is the research workflow itself.

---

# Our Solution

Rather than building another analytics dashboard, we are building a research platform.

The platform continuously transforms:

Raw Blockchain Events

↓

Structured Data

↓

Research Features

↓

Experiments

↓

Knowledge

↓

Research Decisions

This shifts the researcher’s role from collecting information to evaluating hypotheses.

---

# Success Looks Like

Instead of asking:

"Where can I find this information?"

Researchers should ask:

"What hypothesis do I want to test?"

The platform should automatically provide the required data, historical context, engineered features, and research artifacts needed to answer that question.

---

# Guiding Principle

The goal of this project is not to collect more blockchain data.

The goal is to reduce the friction between curiosity and validated knowledge.

Every design decision should be evaluated against a single question:

> Does this make high-quality quantitative research faster, more reliable, and more reproducible?
```
