---
name: model-requirements-grill
description: Elicit, recommend, confirm, and record a model's business contract and design decisions before implementation. Use for a new model or when purpose, inputs, outputs, acceptance criteria, formulas, parameters, measurements, or boundary behavior changes.
---

# Model Requirements Grill

Read the target `model.yaml`, `decisions.yaml`, `model-spec.md`, and current
`model-lab status` report. For a new model, load only the requirement and design
sections of `docs/10-流程层/11-新模型开发SOP.md`.

Use the task type and chosen interaction mode from `$model-lab-router`; follow the
scenario table in `docs/00-入口层/06-模型任务问答与交互档位.md`. Discover facts from
available files and tools. Ask the user only for missing business facts or
decisions. Work in dependency-aware rounds: ask every currently independent
question, give one recommended answer per question, and postpone dependent questions
to the next round. Never reopen the mode selector here.

For a **new model**, cover only the unresolved current items for the `specified`
transition:

- purpose, users, frequency, scope, and non-goals;
- input and output names, types, units, and meanings;
- measurable acceptance criteria;
- formula or model logic;
- parameters, sources, units, adjustability, and measurement mapping;
- missing, abnormal, and boundary behavior;
- target-platform facts added by the GPU or Chronos adapter.

For an **existing model contract or behavior upgrade**, compare the parent
contract, release evidence, and proposed version first. Ask only for changed or
conflicting fields and their acceptance criteria; reuse unchanged confirmed
facts. For a **behavior-preserving repair**, do not run a fresh new-model
questionnaire. Stop only if the old behavior baseline or actual online
signature is uncertain or contradicts the claimed no-change scope, then ask
for the specific business choice. Do not treat missing technical evidence as
a request for the user to perform a check that tools can run.

After explicit confirmation, write the resolved facts to `model.yaml` and
`model-spec.md`. Update the matching `decisions.yaml` entry with the full
recommendation, full answer, `confirmed` status, confirmer identity, and an ISO 8601
timestamp with timezone. Use the model owner as confirmer unless the user supplies a
different identity. Do not separate business, technical, and release roles.

Never save “按推荐” as the answer. A single “全部按推荐” applies only to the
recommendations actually presented together, not future evidence-dependent
confirmations. An explicit task to create or develop this model
covers local `new --apply` and gated `advance --apply` within the agreed workspace;
do not interrupt for a separate authorization at every transition. Finish by
rerunning `model-lab status`; do not bypass a failed gate by editing
`model.lifecycle_state` directly.
