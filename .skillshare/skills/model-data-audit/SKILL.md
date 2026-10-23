---
name: model-data-audit
description: Audit model data, measurements, quality, and anomaly handling before a build decision is confirmed. Use when a model needs source-data inspection, field mapping, quality evidence, or a decision about missing, abnormal, stale, or physically implausible values.
---

> contract: workflow=model-lifecycle-v1 steps=data-source,data-quality

# Model Data Audit

Use the scenario and interaction mode already selected by `$model-lab-router`;
do not open a new mode selector. For a standalone read-only data audit, report
the measured facts without a confirmation dialog. See the data-audit row in
`docs/00-入口层/06-模型任务问答与交互档位.md`.

Read the target model contract and only the data sources relevant to that model. Treat
measured distributions, missingness, duplicates, freezes, jumps, time coverage, and
physical bounds as facts to calculate rather than questions for the user.

Record reproducible commands, data windows, counts, units, and conclusions without
committing raw plant data, credentials, or large generated datasets. If data cannot be
accessed, ask for the approved source or evidence location; do not ask the user to
manually calculate facts that tools can derive.

Interaction is required only when the observed facts create a business choice, such
as excluding a period, accepting a known instrument defect, changing a measurement
mapping, imputing a missing signal, or relaxing a physical limit. Present the evidence,
impact, and recommended treatment for all currently independent choices in one
round, then wait for confirmation. Do not ask the user to approve each data query.

After confirmation, update the data-quality section of `model-spec.md` and the
matching `build-data-quality-treatment` decision with the complete recommendation and
answer. Do not change the algorithm, contract, or threshold silently. Rerun
`model-lab status <workspace>` and leave unrelated later-stage decisions open.
