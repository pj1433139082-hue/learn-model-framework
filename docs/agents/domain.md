# Domain Docs

本仓库采用单上下文领域文档布局。

## Before exploring

- 阅读根目录 `CONTEXT.md`，使用其中的规范术语。
- 阅读 `docs/adr/` 中与当前工作相关的决策。
- 如果工作与 ADR 冲突，必须显式指出，不能静默覆盖。

## Layout

```text
/
├── CONTEXT.md
└── docs/
    └── adr/
```

`CONTEXT.md` 只维护领域词汇；规格、实现计划和运行记录不得混入词汇表。只有难以逆转、缺少背景会令人意外且确有取舍的决策才进入 ADR。
