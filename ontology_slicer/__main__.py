"""命令行。

    python -m ontology_slicer inspect --ontology fixtures/ontology.json
    python -m ontology_slicer slice   --ontology fixtures/ontology.json \
                                      --metric 成交额 --dim 城市 --domain ord --role role:hq
    python -m ontology_slicer serve   --ontology fixtures/ontology.json --port 8765
"""

from __future__ import annotations

import argparse
import json
import sys

from .errors import SliceError
from .ontology import Ontology
from .server import serve
from .view import slice_terms, slice_view


def _load(path: str) -> Ontology:
    try:
        return Ontology.load(path)
    except SliceError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2) from None


def _cmd_inspect(args: argparse.Namespace) -> int:
    ont = _load(args.ontology)
    metrics = sorted(ont.metrics.values(), key=lambda m: (m.domain, m.name))
    print(f"本体 version={ont.version}")
    print(f"域：{', '.join(ont.domains)}")
    print(f"表 {len(ont.tables)}  关系 {len(ont.relations)}  "
          f"指标 {len(ont.metrics)}  属性 {len(ont.properties)}  "
          f"黑话 {len(ont.glossary)}  口径组 {len(ont.groups)}")
    print()
    print(f"{'域':10} {'指标':22} {'显示名':16} {'别名':30} 可见范围")
    for m in metrics:
        print(f"{m.domain:10} {m.name:22} {m.display:16} "
              f"{','.join(m.aliases)[:30]:30} {','.join(m.visibility_scope)}")
    if ont.groups:
        print()
        print("口径组：")
        for group, members in sorted(ont.groups.items()):
            defaults = [n for n in members if ont.metrics[n].default_in_group]
            tag = f"默认={defaults[0]}" if defaults else "无默认（必须追问）"
            print(f"  {group}: {', '.join(members)}  —  {tag}")
    return 0


def _cmd_slice(args: argparse.Namespace) -> int:
    ont = _load(args.ontology)
    request: dict = {"metric_terms": args.metric}
    if args.dim:
        request["dim_terms"] = args.dim
    if args.domain:
        request["domain"] = args.domain
    if args.role:
        request["principal"] = {"subject": args.role}
    if args.max_metrics:
        request["max_metrics"] = args.max_metrics
    try:
        out = slice_view(ont, request)
    except SliceError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"view_hash  {out['view_hash']}")
    print(f"指标 {len(out['view']['metrics'])}  属性 {len(out['view']['properties'])}  "
          f"关系 {len(out['view']['relations'])}")
    for group in ("matched", "ambiguous", "unmatched", "conflicts"):
        for item in out[group]:
            print(f"{group:10} {json.dumps(item, ensure_ascii=False)}")
    for field in ("metrics", "properties"):
        if out["truncated"][field]:
            print(f"truncated  {field}: {', '.join(out['truncated'][field])}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    serve(_load(args.ontology), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontology_slicer", description="本体切片器")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="看本体的指标、口径组和可见范围")
    p_inspect.add_argument("--ontology", required=True)
    p_inspect.set_defaults(func=_cmd_inspect)

    p_slice = sub.add_parser("slice", help="对一组词做一次切片")
    p_slice.add_argument("--ontology", required=True)
    p_slice.add_argument("--metric", action="append", required=True, metavar="词")
    p_slice.add_argument("--dim", action="append", default=[], metavar="词")
    p_slice.add_argument("--domain", default=None)
    p_slice.add_argument("--role", default=None, metavar="role:xxx",
                         help="主体角色，必须以 role: 开头，例如 role:hq")
    p_slice.add_argument("--max-metrics", type=int, default=None, dest="max_metrics")
    p_slice.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_slice.set_defaults(func=_cmd_slice)

    p_serve = sub.add_parser("serve", help="起一个无状态 HTTP 服务")
    p_serve.add_argument("--ontology", required=True)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
