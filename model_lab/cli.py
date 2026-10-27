from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from model_lab.catalog import (
    build_catalog,
    catalog_digest,
    catalog_is_in_sync,
    render_catalog_markdown,
)
from model_lab.deployment import (
    ContractViolationError,
    DryRunPlan,
    PublishGateResult,
    build_dry_run_plan,
    evaluate_publish_gate,
)
from model_lab.manifest import (
    AssetSummary,
    ManifestError,
    ValidationReport,
    inspect_manifest,
    validate_manifest,
)
from model_lab.lifecycle import LifecycleReport, advance_workspace, workspace_status
from model_lab.maintenance import MaintenanceReport, fork_maintenance
from model_lab.draft_upgrade import DraftUpgradeReport, fork_draft_contract
from model_lab.release_gate import run_release_gate
from model_lab.repository_doctor import run_repository_doctor
from model_lab.runtime_contract import (
    ACTIVE_RUNTIME_PROFILE_IDS,
    DEFAULT_RUNTIME_PROFILE_ID,
)
from model_lab.company_publish import PublishExecutionResult, publish_fixed_bundle, verify_existing_publication
from model_lab.publication_import import PublicationImportError, import_group_publication
from model_lab.input_contract import validate_inputs
from model_lab.scaffolding import (
    ScaffoldReport,
    ScaffoldRequest,
    WorkspaceError,
    scaffold_model,
)
from model_lab.preflight import run_bundle_preflight
from model_lab.bundle_build import BundleBuildError, build_bundle
from model_lab.test_profiles import PROFILE_NAMES, run_test_profile
from model_lab.workflow_state import (
    model_manifests,
    task_context,
    write_snapshot,
)
from model_lab.environment import configured_models_root, ensure_external_workspace, framework_root, framework_identity
from model_lab.setup import setup_workspace
from model_lab.task_plan import read_task_plan, write_task_plan
from model_lab.record_recovery import framework_recovery_command, refresh_snapshot


def _configured_models_root(value: str | None) -> Path:
    try:
        return configured_models_root(value)
    except ValueError as error:
        raise WorkspaceError(getattr(error, "code", "models_root_required"), str(error)) from error


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-lab")
    parser.add_argument("--version", action="version", version=str(framework_identity()["version"]))
    commands = parser.add_subparsers(dest="command", required=True)

    setup_parser = commands.add_parser("setup", help="一次本机配置：私有目录、项目技能与框架来源")
    setup_parser.add_argument("--models-root", help="仓库外的私有模型根目录；省略时复用配置")
    setup_parser.add_argument("--apply", action="store_true", help="应用本机配置与技能同步；默认预览")
    setup_parser.add_argument("--update", action="store_true", help="拉取 main 后同步本机设置并记录框架提交")
    setup_parser.add_argument("--format", choices=("human", "json"), default="human")

    task_parser = commands.add_parser("task-plan", help="记录或恢复单个任务的交互档位与下一步")
    task_parser.add_argument("workspace", help="私有模型工作区")
    task_parser.add_argument("--task-id", required=True, help="本次任务唯一的小写英文 ID")
    task_parser.add_argument("--mode", choices=("full-auto", "assisted", "manual-review"))
    task_parser.add_argument("--intent", choices=("new", "maintain", "build", "publish", "status", "read", "data-audit"))
    task_parser.add_argument("--next-action", help="下一步；不包含凭据或永久发布许可")
    task_parser.add_argument("--pending", action="append", default=[], help="待回答决策 ID，可重复")
    task_parser.add_argument("--apply", action="store_true", help="保存任务计划；默认只读或预览")
    task_parser.add_argument("--format", choices=("human", "json"), default="human")

    new_parser = commands.add_parser(
        "new", help="规划或创建一个版本化模板驱动的模型工作区"
    )
    new_parser.add_argument("model_id", help="小写 ASCII kebab-case 模型 ID")
    new_parser.add_argument("--name", required=True, help="模型显示名称")
    new_parser.add_argument("--owner", required=True, help="模型负责人")
    new_parser.add_argument(
        "--target",
        choices=("cpu-iot", "gpu", "chronos"),
        default="cpu-iot",
        help="部署目标模板（默认：cpu-iot）",
    )
    new_parser.add_argument(
        "--models-root",
        help="私有模型目录根路径；也可设置 MODEL_LAB_MODELS_ROOT（不得位于本框架仓库内）",
    )
    new_parser.add_argument(
        "--runtime-profile",
        choices=ACTIVE_RUNTIME_PROFILE_IDS,
        default=DEFAULT_RUNTIME_PROFILE_ID,
        help=(
            "目标运行环境模板（默认："
            f"{DEFAULT_RUNTIME_PROFILE_ID}）"
        ),
    )
    new_parser.add_argument(
        "--serving-mode",
        choices=("numeric", "point-code"),
        default="numeric",
        help="平台推理入参模式（默认：numeric；point-code 需填写测点绑定与时间规则）",
    )
    new_parser.add_argument("--gpu-compute-capability", help="GPU 算力版本范围")
    new_parser.add_argument(
        "--gpu-memory-gb-min", type=float, help="最低 GPU 显存（GiB）"
    )
    new_parser.add_argument("--cuda-version", help="CUDA 版本范围")
    new_parser.add_argument("--framework-name", help="运行框架名称")
    new_parser.add_argument("--framework-version", help="运行框架版本范围")
    new_parser.add_argument(
        "--artifact-format",
        choices=("onnx", "safetensors", "torchscript"),
        help="模型制品格式",
    )
    new_parser.add_argument(
        "--apply",
        action="store_true",
        help="显式创建工作区；省略时仅输出计划",
    )
    new_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    status_parser = commands.add_parser(
        "status", help="只读报告模型工作区状态与下一阶段门禁"
    )
    status_parser.add_argument("workspace", help="模型工作区或 model.yaml 路径")
    status_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    advance_parser = commands.add_parser(
        "advance", help="门禁通过后规划或应用一个本地生命周期转换"
    )
    advance_parser.add_argument("workspace", help="模型工作区或 model.yaml 路径")
    advance_parser.add_argument(
        "--apply",
        action="store_true",
        help="显式写入下一生命周期状态；省略时仅输出计划",
    )
    advance_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    inspect_parser = commands.add_parser(
        "inspect", help="查看模型资产清单中的身份与契约"
    )
    inspect_parser.add_argument("manifest", help="模型资产清单路径")
    inspect_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    validate_parser = commands.add_parser(
        "validate", help="验证模型资产清单是否满足契约"
    )
    validate_parser.add_argument("manifest", help="模型资产清单路径")
    validate_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    dry_run_parser = commands.add_parser(
        "dry-run", help="生成不访问远端平台的确定性部署计划"
    )
    dry_run_parser.add_argument("manifest", help="模型资产清单路径")
    dry_run_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    publish_parser = commands.add_parser(
        "publish", help="检查显式发布授权并执行安全发布门禁"
    )
    publish_parser.add_argument("manifest", help="模型资产清单路径")
    publish_parser.add_argument(
        "--target", help="可选：显式目标，省略时使用 model.yaml 中的目标"
    )

    context_parser = commands.add_parser(
        "context", help="按模型版本生成或检查可提交的工作流状态快照，并给出最小任务文档"
    )
    context_parser.add_argument("workspace", nargs="?", help="模型工作区或 model.yaml；--all 时省略")
    context_parser.add_argument("--all", action="store_true", help="遍历指定私有模型目录下全部模型版本")
    context_parser.add_argument("--models-root", help="私有模型目录；--all 时也可使用 MODEL_LAB_MODELS_ROOT")
    context_parser.add_argument("--task", choices=("status", "read", "new", "maintain", "build", "publish"), default="status")
    context_action = context_parser.add_mutually_exclusive_group()
    context_action.add_argument("--write", action="store_true", help="写入从事实源重新生成的 workflow-state.json")
    context_action.add_argument("--check", action="store_true", help="只读检测已提交快照是否漂移")
    context_parser.add_argument("--format", choices=("human", "json"), default="human")

    maintain_parser = commands.add_parser(
        "maintain", help="从已发布模型规划或创建 Python 3.11 补丁或在线契约升级工作区"
    )
    maintain_parser.add_argument("workspace", help="已发布模型目录或 model.yaml 路径")
    maintain_parser.add_argument("--version", required=True, help="新版本；补丁如 1.0.1，契约升级如 1.1.0")
    maintain_parser.add_argument(
        "--change-kind", choices=("behavior-unchanged", "contract-upgrade"),
        default="behavior-unchanged", help="默认行为不变补丁；契约升级须单独次版本",
    )
    maintain_parser.add_argument("--apply", action="store_true", help="显式创建维护工作区；省略时仅预览")
    maintain_parser.add_argument("--format", choices=("human", "json"), default="human")
    draft_fork_parser = commands.add_parser(
        "draft-fork", help="从未发布的数值草稿安全分叉测点输入契约次版本"
    )
    draft_fork_parser.add_argument("workspace", help="原始 models/_drafts/<model-id> 草稿")
    draft_fork_parser.add_argument("--version", required=True, help="下一次版本，例如 0.2.0")
    draft_fork_parser.add_argument("--apply", action="store_true", help="显式创建新草稿；省略时仅预览")
    draft_fork_parser.add_argument("--format", choices=("human", "json"), default="human")
    publish_parser.add_argument(
        "--experiment", help="平台绑定页的精确实验名（含版本后缀）"
    )
    publish_parser.add_argument(
        "--owner", help="发布作者标识/账号标签（不是登录凭据）"
    )
    publish_parser.add_argument(
        "--authorize-publish",
        action="store_true",
        help="明确授权本次指定模型与实验的发布",
    )
    publish_parser.add_argument(
        "--company-package",
        help="经清单摘要核验的公司技能包 ZIP；仅 --apply 时读取",
    )
    publish_parser.add_argument(
        "--apply", action="store_true", help="通过门禁后上传固定 MLflow 实包并回载核验",
    )
    publish_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    catalog_parser = commands.add_parser(
        "catalog", help="从模型清单生成或校验中央资产目录"
    )
    catalog_parser.add_argument("models_root", help="包含模型子目录的根路径")
    catalog_parser.add_argument(
        "--expected-count",
        type=int,
        help="可选：预期在役模型数；省略时只统计实际发现的模型",
    )
    catalog_parser.add_argument(
        "--check",
        metavar="PATH",
        help="检查已生成的 Markdown 目录是否漂移",
    )
    catalog_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="输出格式（默认：markdown）",
    )

    release_gate_parser = commands.add_parser(
        "release-gate", help="离线执行第一阶段仓库发布门禁"
    )
    release_gate_parser.add_argument(
        "repository_root",
        nargs="?",
        default=str(framework_root()),
        help="框架根路径（默认：当前实际加载的框架）",
    )
    release_gate_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    input_check_parser = commands.add_parser(
        "input-check", help="核对 inputs.md 的 IoT/化验/人工混合来源声明"
    )
    input_check_parser.add_argument("workspace", help="模型工作区路径")
    input_check_parser.add_argument("--format", choices=("human", "json"), default="human")
    publication_parser = commands.add_parser(
        "publication", help="登记集团部署回执；只写本地回执，不执行上传"
    )
    publication_commands = publication_parser.add_subparsers(
        dest="publication_command", required=True
    )
    publication_import_parser = publication_commands.add_parser(
        "import", help="登记一个已验证的集团 Run 回执"
    )
    publication_import_parser.add_argument("workspace", help="模型工作区路径")
    publication_import_parser.add_argument("--run-id", required=True, help="集团部署返回的 MLflow Run ID")
    publication_import_parser.add_argument("--experiment", required=True, help="平台实验名")
    publication_import_parser.add_argument("--company-package", required=True, help="集团 infra_model_deploy.zip")
    publication_import_parser.add_argument("--deploy-kit", help="scripts/deploy-kit/<model-id> 目录；用于计算 kit SHA")
    publication_import_parser.add_argument("--format", choices=("human", "json"), default="human")
    verify_publication_parser = commands.add_parser(
        "verify-publication", help="只读复核已有 MLflow Run；完全通过后补本地结构化回执"
    )
    verify_publication_parser.add_argument("manifest", help="模型资产清单路径")
    verify_publication_parser.add_argument("--experiment", required=True, help="平台实验名，含版本后缀")
    verify_publication_parser.add_argument("--run-id", required=True, help="已有 FINISHED Run ID")
    verify_publication_parser.add_argument("--company-package", required=True, help="清单摘要固定的公司 ZIP")
    verify_publication_parser.add_argument("--format", choices=("human", "json"), default="human")

    doctor_parser = commands.add_parser(
        "doctor", help="只读检查当前项目是否真正使用 Python 3.11"
    )
    doctor_parser.add_argument(
        "repository_root",
        nargs="?",
        default=str(framework_root()),
        help="框架根路径（默认：当前实际加载的框架）",
    )
    doctor_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )
    doctor_parser.add_argument("--company-package", help="外部公司 Python 3.11 ZIP；仅静态核对，不执行")
    doctor_parser.add_argument("--target-platform", choices=("linux_x86_64", "windows_x86_64"), help="公司运行时目标；默认当前平台")

    profile_parser = commands.add_parser(
        "test-profile",
        help="按本机、目标 Linux 和真实 IoT 分档运行测试并报告跳过原因",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "档位说明（四档均不上传模型）：\n"
            "  local            本机 Python 3.11 仓库回归；从这里开始，默认不联网。\n"
            "  linux-offline    Linux 3.11 + 经核验公司 ZIP；离线构建和预测。\n"
            "  linux-online     Linux 3.11 + 公司内网；显式开启真实 IoT 冒烟。\n"
            "  windows-network  显式开启公司 IoT 的 Windows 主机网络诊断；不代替 Linux 验收。\n"
            "详细环境变量与验收边界见 docs/10-流程层/13-运行环境与MLflow制品SOP.md。"
        ),
    )
    profile_parser.add_argument(
        "profile", choices=PROFILE_NAMES, help="先运行 local，再按目标环境逐档验收"
    )
    profile_parser.add_argument("--format", choices=("human", "json"), default="human")

    bundle_parser = commands.add_parser(
        "bundle-preflight", help="静态检查 Python 3.11 构建环境与 MLflow 发布包"
    )
    bundle_parser.add_argument("manifest", help="模型资产清单路径")
    bundle_parser.add_argument(
        "--bundle",
        help="MLflow service 目录；默认使用模型目录下 release-bundle/service",
    )
    bundle_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="输出格式（默认：human）",
    )

    build_parser = commands.add_parser(
        "bundle-build", help="从单模型源码和样本构建本地 MLflow service 包"
    )
    build_parser.add_argument("manifest", help="模型工作区或 model.yaml 路径")
    build_parser.add_argument("--sample-csv", required=True, help="本地 CSV 样本路径")
    build_parser.add_argument("--iot-package-zip", help="经摘要核验的公司 Python 3.11 IoT ZIP，仅测点模式使用")
    build_parser.add_argument(
        "--apply", action="store_true", help="显式构建；省略时仅预览"
    )
    build_parser.add_argument(
        "--format", choices=("human", "json"), default="human"
    )
    return parser


def _configure_utf8_stdio() -> None:
    """Force UTF-8 output even when ``python -I`` ignores PYTHONUTF8 on Windows."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(arguments: Sequence[str] | None = None) -> int:
    _configure_utf8_stdio()
    raw_arguments = tuple(arguments) if arguments is not None else tuple(sys.argv[1:])
    command = raw_arguments[0] if raw_arguments else None
    if sys.version_info[:2] != (3, 11) and command != "doctor":
        detected = ".".join(str(part) for part in sys.version_info[:3])
        print(
            "model-lab: this project only runs work commands on Python 3.11; "
            f"detected Python {detected}. Run model-lab doctor with the selected "
            "interpreter, then switch to .venv-311.",
            file=sys.stderr,
        )
        return 8
    parser = _build_parser()
    options = parser.parse_args(raw_arguments)

    if options.command == "setup":
        try:
            result = setup_workspace(options.models_root, apply=options.apply, update=options.update)
        except (OSError, ValueError) as error:
            print(f"model-lab setup: {error}", file=sys.stderr)
            return 2
        if options.format == "json":
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print(f"setup: {result['status']}")
            print(f"Framework: {result['framework']['root']}")
            print(f"Source: {result['framework']['version']} | {result['framework'].get('git_commit') or 'unknown commit'} | dirty={result['framework'].get('git_dirty')}")
            if "models_root" in result:
                print(f"Private models: {result['models_root'] or 'not configured'}")
            if "skills" in result:
                print(f"Skills: {result['skills']['status']}")
            if "hooks" in result:
                print(f"Hook: {result['hooks']['state']} (AGENTS.md + context/status remain available)")
            if result.get("update"):
                print(f"Update: {result['update']['previous_commit'] or 'unknown'} -> {result['update']['current_commit'] or 'unknown'}")
            print(f"next: {result['next_action']}")
        return 0 if result["valid"] else 2

    if options.command == "task-plan":
        fields = (options.mode, options.intent, options.next_action)
        if any(fields) and not all(fields):
            parser.error("task-plan updates require --mode, --intent and --next-action together")
        if options.apply and not all(fields):
            parser.error("task-plan --apply requires the task's mode, intent and next action")
        try:
            ensure_external_workspace(options.workspace)
            if all(fields):
                result = {"status": "planned", "task_id": options.task_id, "mode": options.mode,
                          "intent": options.intent, "next_action": options.next_action,
                          "pending_decisions": options.pending}
                if options.apply:
                    path = write_task_plan(options.workspace, options.task_id, mode=options.mode,
                                           intent=options.intent, next_action=options.next_action,
                                           pending_decisions=tuple(options.pending))
                    result = {"status": "recorded", "path": str(path),
                              "task": read_task_plan(options.workspace, options.task_id)}
            else:
                result = {"status": "recorded", "task": read_task_plan(options.workspace, options.task_id)}
                if result["task"] is None:
                    result["status"] = "not_recorded"
        except (OSError, ValueError, yaml.YAMLError) as error:
            print(f"model-lab task-plan: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, sort_keys=True) if options.format == "json"
              else json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if options.command == "context":
        if options.all == bool(options.workspace):
            parser.error("context requires exactly one workspace or --all")
        try:
            targets = model_manifests(_configured_models_root(options.models_root)) if options.all else (options.workspace,)
            records: list[dict[str, object]] = []
            for target in targets:
                if options.write:
                    write_snapshot(target)
                context = task_context(target, task=options.task)
                snapshot = context["snapshot"]
                assert isinstance(snapshot, dict)
                model = snapshot["model"]
                assert isinstance(model, dict)
                records.append({
                    "workspace": str(target), "model_id": model["id"],
                    "version": model["version"], "lifecycle_state": model["lifecycle_state"],
                    "snapshot_in_sync": context["snapshot_in_sync"],
                    "publication": snapshot["publication"], "serving": snapshot["serving"],
                    "docs": context["docs"] if not options.all else [],
                    "doc_paths": context["doc_paths"] if not options.all else [],
                    "framework": context["framework"],
                })
        except (OSError, ValueError, yaml.YAMLError) as error:
            print(f"model-lab context: {error}", file=sys.stderr)
            return 2
        valid = all(item["snapshot_in_sync"] for item in records)
        if options.format == "json":
            print(json.dumps({"valid": valid, "models": records}, ensure_ascii=False, sort_keys=True))
        else:
            for item in records:
                print(f"{item['model_id']} {item['version']}: {item['lifecycle_state']} | "
                      f"snapshot {'in sync' if item['snapshot_in_sync'] else 'DRIFTED'} | "
                      f"publication {item['publication']['status']}")
                print(f"  Framework: {item['framework']['root']} | {item['framework']['version']} | {item['framework']['git_commit'] or 'unknown commit'} | dirty={item['framework']['git_dirty']}")
                for doc in item["doc_paths"]:
                    print(f"  - {doc}")
        return 3 if options.check and not valid else 0

    if options.command == "input-check":
        report = validate_inputs(options.workspace)
        if options.format == "json":
            print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(f"input-check: {'PASS' if report.valid else 'FAIL'} ({report.rows} rows)")
            for issue in report.issues:
                print(f"- {issue.code} {issue.input_name}: {issue.detail}")
        return 0 if report.valid else 1
    if options.command == "publication":
        if options.publication_command != "import":
            parser.error(f"unsupported publication command: {options.publication_command}")
        try:
            result = import_group_publication(
                options.workspace,
                run_id=options.run_id,
                experiment=options.experiment,
                company_package=options.company_package,
                deploy_kit=options.deploy_kit,
            )
        except (OSError, ValueError, PublicationImportError) as error:
            code = getattr(error, "code", "publication_import_failed")
            print(f"model-lab publication import: {code}: {error}", file=sys.stderr)
            return 2
        if options.format == "json":
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print(f"publication import: {result['status']}")
            print(f"receipt: {result['receipt']}")
        return 0
    if options.command == "verify-publication":
        try:
            ensure_external_workspace(options.manifest)
            result = verify_existing_publication(
                options.manifest, experiment=options.experiment,
                run_id=options.run_id, company_package=options.company_package,
            )
        except (OSError, ValueError, yaml.YAMLError) as error:
            print(f"model-lab verify-publication: {error}", file=sys.stderr)
            return 2
        if options.format == "json":
            print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(result.as_text())
        return 2 if result.local_record_incomplete else 0 if result.status == "verified" else 4

    if options.command in {"new", "maintain", "draft-fork", "status", "advance"}:
        try:
            workspace_result: ScaffoldReport | MaintenanceReport | DraftUpgradeReport | LifecycleReport
            if options.command == "new":
                workspace_result = scaffold_model(
                    ScaffoldRequest(
                        model_id=options.model_id,
                        name=options.name,
                        owner=options.owner,
                        target=options.target,
                        serving_mode=options.serving_mode,
                        runtime_profile=options.runtime_profile,
                        gpu_compute_capability=options.gpu_compute_capability,
                        gpu_memory_gb_min=options.gpu_memory_gb_min,
                        cuda_version=options.cuda_version,
                        framework_name=options.framework_name,
                        framework_version=options.framework_version,
                        artifact_format=options.artifact_format,
                    ),
                    _configured_models_root(options.models_root),
                    apply=options.apply,
                )
                exit_code = 0
            elif options.command == "maintain":
                workspace_result = fork_maintenance(
                    options.workspace, options.version,
                    change_kind=options.change_kind, apply=options.apply,
                )
                exit_code = 0
            elif options.command == "draft-fork":
                workspace_result = fork_draft_contract(
                    options.workspace, options.version, apply=options.apply,
                )
                exit_code = 0
            elif options.command == "status":
                workspace_result = workspace_status(options.workspace)
                exit_code = 0
            else:
                lifecycle_result = advance_workspace(
                    options.workspace,
                    apply=options.apply,
                )
                workspace_result = lifecycle_result
                exit_code = (
                    0
                    if lifecycle_result.can_advance or lifecycle_result.applied
                    else 3
                )
        except WorkspaceError as error:
            if options.format == "json":
                error_payload = {
                    "error": {"code": error.code, "message": str(error)}
                }
                print(
                    json.dumps(error_payload, ensure_ascii=False, sort_keys=True),
                    file=sys.stderr,
                )
            else:
                print(f"model-lab: {error}", file=sys.stderr)
            return 2
        record_result = None
        recovery_command: tuple[str, ...] = ()
        if options.command in {"new", "maintain", "draft-fork"} and workspace_result.status == "created":
            record_path = workspace_result.destination
        elif options.command == "advance" and workspace_result.applied:
            record_path = workspace_result.manifest
        else:
            record_path = None
        if record_path is not None:
            record_result = refresh_snapshot(record_path)
            recovery_command = framework_recovery_command("context", str(record_path), "--write", "--format", "json")
            if not record_result.complete:
                exit_code = 2
                print(record_result.as_text(command=recovery_command), file=sys.stderr)
        if options.format == "json":
            payload = workspace_result.as_dict()
            if record_result is not None:
                payload.update(record_result.details(action="refresh_snapshot", command=recovery_command))
            print(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True
                )
            )
        else:
            print(workspace_result.as_text())
            if record_result is not None and record_result.complete and record_result.snapshot_attempts > 1:
                print(record_result.as_text(command=recovery_command))
        return exit_code

    if options.command == "release-gate":
        release_report = run_release_gate(options.repository_root)
        if options.format == "json":
            print(
                json.dumps(
                    release_report.as_dict(), ensure_ascii=False, sort_keys=True
                )
            )
        else:
            print(release_report.as_text())
        return 0 if release_report.valid else 5

    if options.command == "doctor":
        doctor_report = run_repository_doctor(options.repository_root, company_package=options.company_package,
                                             target_platform=options.target_platform)
        if options.format == "json":
            print(json.dumps(doctor_report.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(doctor_report.as_text())
        return 0 if doctor_report.valid else 6

    if options.command == "test-profile":
        test_report = run_test_profile(options.profile)
        if options.format == "json":
            print(json.dumps(test_report.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(test_report.as_text())
        return 0 if test_report.valid else 9

    if options.command == "bundle-preflight":
        try:
            preflight_report = run_bundle_preflight(options.manifest, options.bundle)
        except ManifestError as error:
            if options.format == "json":
                print(
                    json.dumps(
                        {"error": {"code": error.code, "message": str(error)}},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
            else:
                print(f"model-lab: {error}", file=sys.stderr)
            return 2
        if options.format == "json":
            print(json.dumps(preflight_report.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(preflight_report.as_text())
        return 0 if preflight_report.valid else 7

    if options.command == "bundle-build":
        try:
            build_report = build_bundle(
                options.manifest, options.sample_csv, apply=options.apply,
                iot_package_zip=options.iot_package_zip,
            )
        except BundleBuildError as error:
            if options.format == "json":
                print(
                    json.dumps(
                        {"error": {"code": error.code, "message": str(error)}},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
            else:
                print(f"model-lab: {error}", file=sys.stderr)
            return 2
        if options.format == "json":
            print(json.dumps(build_report.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(build_report.as_text())
        return 0

    if options.command == "catalog":
        report = build_catalog(
            options.models_root,
            expected_count=options.expected_count,
        )
        in_sync = (
            catalog_is_in_sync(report, options.check)
            if options.check is not None and report.valid
            else None
        )
        valid = report.valid and in_sync is not False
        if options.format == "json":
            payload = report.as_dict()
            payload["valid"] = valid
            payload["catalog_in_sync"] = in_sync
            issues_payload = [issue.as_dict() for issue in report.issues]
            if in_sync is False:
                issues_payload.append(
                    {
                        "code": "catalog_output_drift",
                        "path": str(options.check),
                        "message": "generated catalog does not match model manifests",
                        "suggestion": "regenerate the catalog from current manifests",
                    }
                )
            payload["issues"] = issues_payload
            if report.valid:
                markdown = render_catalog_markdown(report)
                payload["catalog_sha256"] = catalog_digest(markdown)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        elif not report.valid:
            print(report.as_text())
        elif options.check is not None:
            state = "IN SYNC" if in_sync else "DRIFTED"
            print(f"catalog: {state}")
        else:
            print(render_catalog_markdown(report), end="")
        return 0 if valid else 3

    if options.command in {"inspect", "validate", "dry-run", "publish"}:
        result: AssetSummary | ValidationReport | DryRunPlan | PublishGateResult | PublishExecutionResult
        try:
            if options.command == "inspect":
                result = inspect_manifest(options.manifest)
                exit_code = 0
            elif options.command == "validate":
                validation = validate_manifest(options.manifest)
                result = validation
                exit_code = 0 if validation.valid else 3
            elif options.command == "dry-run":
                result = build_dry_run_plan(options.manifest)
                exit_code = 0
            else:
                result = evaluate_publish_gate(
                    options.manifest,
                    target=options.target,
                    experiment=options.experiment,
                    owner=options.owner,
                    authorized=options.authorize_publish,
                )
                exit_code = 0 if result.status == "ready" else 4
                if options.apply and result.status == "ready":
                    result = publish_fixed_bundle(
                        options.manifest,
                        experiment=options.experiment,
                        owner=options.owner,
                        company_package=options.company_package or "",
                    )
                    exit_code = 2 if result.local_record_incomplete else 0 if result.status == "published" else 4
        except ContractViolationError as error:
            if options.format == "json":
                print(
                    json.dumps(
                        error.report.as_dict(), ensure_ascii=False, sort_keys=True
                    )
                )
            else:
                print(error.report.as_text())
            return 3
        except ManifestError as error:
            if options.format == "json":
                payload = {"error": {"code": error.code, "message": str(error)}}
                print(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    file=sys.stderr,
                )
            else:
                print(f"model-lab: {error}", file=sys.stderr)
            return 2
        if options.format == "json":
            print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(result.as_text())
        return exit_code

    parser.error(f"unsupported command: {options.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
