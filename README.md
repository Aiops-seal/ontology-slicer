# ontology-slicer

本体切片器。给模型看的不是整份本体，是切出来的一块。

```python
from ontology_slicer import Ontology, slice_terms

ont = Ontology.load("fixtures/ontology.json")
out = slice_terms(ont, ["成交额"], dim_terms=["城市"], subject="role:hq")
```

三件输入：**本体**（全集）、**意图词**（相关）、**角色**（可见）。输出是它们的交集。

---

## 它解决的是一个具体问题

`fixtures/ontology.json` 里有 12 个指标，其中两个都叫「成交额」：

| 指标 | 域 | 口径 | 差异 |
|---|---|---|---|
| `gmv` | ord | 含退款，未扣优惠券 | 两者差两三个百分点 |
| `gmv_settle` | settle | 不含退款，来自结算单 | 同上 |

把 12 个指标全塞给模型，它会挑一个。挑错了，SQL 照常执行、数字照常返回、报表上看不出任何异常——**问题不在于模型写错了 SQL，在于它一开始就不知道有两个。**

切片器做的事是：告诉你这两个都成立，你去问用户。

```
$ python -m ontology_slicer slice --ontology fixtures/ontology.json \
    --metric 成交额 --dim 城市

ambiguous  {"term": "成交额", "kind": "metric",
            "candidates": ["gmv", "gmv_settle"], "reason": "family_no_default"}
```

> 注：MQL = Metrics Query Language（指标查询语言），国内已有既成用法（Aloudata 主推 NL2MQL2SQL、北极九章收录为一条技术路线），与 MongoDB Query Language 无关。本仓库是这条路线上的一个小零件，不是它的定义者。

---

## 三条命令

```bash
# 1. 看本体有什么（指标、别名、可见范围、口径组）
python -m ontology_slicer inspect --ontology fixtures/ontology.json

# 2. 对一个词做一次切片
python -m ontology_slicer slice --ontology fixtures/ontology.json \
    --metric 成交额 --dim 城市 --domain ord --role role:hq

# 3. 起服务
python -m ontology_slicer serve --ontology fixtures/ontology.json --port 8765
curl -s localhost:8765/slice -d '{"metric_terms":["订单数"],"dim_terms":["城市"]}'
```

零运行时依赖，Python 3.10+，不需要数据库、不需要模型、不需要 key。

---

## 它会拒绝什么

| 你给 | 它回 |
|---|---|
| `成交额`，不传 `domain` | `ambiguous`，两个候选**都进 view**——只给一个等于没问 |
| `成交额` + `domain=ord` | `matched: gmv`，`gmv_settle` 不进（跨域兄弟不因同组被拉回来） |
| `订单数` | `matched: order_cnt`，`how=default`——本体声明了默认，顺带把 `order_num` 带进 view |
| `GMV` | `ambiguous`——它既是 `gmv` 的名字，也是两个指标的共同别名 |
| `毛利` | `unmatched`，`hint: 本体无此词`，**不猜** |
| `华东` | `unmatched`——这是**值**不是**词**，切片器只认识结构 |
| `同店成交额` + `门店` | `conflicts: dimension_forbidden`——不静默丢弃，单独报出来 |
| `filter_terms` | `UNKNOWN_PARAM`——这个字段被删了，理由见下方 |
| `max_hops` | `UNKNOWN_PARAM`——跳数恒为二，做成参数只会被误用 |
| `principal: {"region_ids":[...]}` | `BAD_PRINCIPAL`——区域是行级条件，属于编译器的 `permissions` |

### 为什么没有 `filter_terms`

它装的是**值**（「华东」「昨天」），值域在数据层不在结构层。切片器手里只有本体，永远无法判断「华东」是不是合法值，最多能猜"本体里有个叫 region 的属性"——而拿值去猜维度，会把一堆不相关的维度塞进 view，直接撞穿 `max_properties`。

收益小、风险大，第一版直接不收。

---

## 三种角色，同一个 hash

```bash
for role in role:hq role:analyst role:finance; do
  python -m ontology_slicer slice --ontology fixtures/ontology.json \
      --metric 退款额 --metric 同店成交额 --role $role --json | grep -E '"name"|"access"'
done
```

| 指标 | `role:hq` | `role:analyst` | `role:finance` |
|---|---|---|---|
| `refund_amount` 退款额 | granted | **denied** | granted |
| `same_store_gmv` 同店成交额 | granted | **denied** | **denied** |

三个角色拿到的 `view_hash` **完全相同**。

- **权限只点亮标记，不改变切片的形状。** 所以「同参数同 hash」这条验收标准才立得住，回归测试才有意义。
- **被 `denied` 的指标仍然在 view 里。** 指标名通常不是秘密，数据才是；藏掉只会让模型说"不知道"，用户听到的是系统坏了。
- **不传 `principal` 时是 `unknown`，不是 `granted`。** 默认放行比默认拒绝危险得多。

代价说清楚：**同一个 hash 不代表同一份可执行的答案。** hash 是结构指纹，权限是执行时的事。

---

## 它明确不做的

| 不做 | 因为 |
|---|---|
| 语义相似 / 同义词 / embedding | 精确匹配敢说"这三个都对，你去问用户"；检索只会还 Top-K 让你随便挑，挑错了也没人知道 |
| 值域校验 | 不知道「华东」是不是合法值 |
| 跨域 JOIN 可行性 | `NO_CANDIDATE_TABLE` 是编译器的错。这里一报，就等于偷偷实现了一半编译器 |
| 排序 / 相关性打分 | 截断按固定优先序，不按"像不像" |
| 自动补 `unmatched` | 一旦开始猜，确定性论证当场失效 |
| 鉴权 / 缓存 / 热更新 | 鉴权是网关的事；热更新会让同一进程在不同时刻给出不同 hash |

**代码 1358 行，测试 116 项。** 薄是设计结果，不是没做完——判断都在本体里、在离线的本体构建阶段，不在这儿。

---

## 本体格式 `v1.1`

和 [`mql-compiler`](https://github.com/Aiops-seal/mql-compiler) **同一份文件，两个读者，零代码共享**。

```json
{
  "version": "1.1",
  "capabilities": {
    "supports_compare": ["yoy", "mom"],
    "min_granularity": "day",
    "max_scan_days": 1800,
    "dialects_verified": ["sqlite"]
  },
  "tables": [], "relations": [],
  "metrics": [{
    "name": "gmv",
    "display": "成交额",
    "domain": "ord",
    "aliases": ["成交额", "GMV"],
    "do_not": ["含退款，不要与 gmv_settle 混用"],
    "visibility_scope": ["*"],
    "dimension_policy": {"mode": "except", "dimensions": ["store"]},
    "supports_compare": true,
    "ambiguity_group": "gmv_family",
    "default_in_group": false,
    "bindings": [{"table": "dwd_order_di", "expr": "SUM({f}.pay_amount)"}]
  }],
  "properties": []
}
```

几个字段是**必填**的，而且看起来多余：

| 字段 | 为什么必填 |
|---|---|
| `aliases` | 可空，但必须写出来——让「这个指标没有别名」成为**声明**而不是**省略** |
| `visibility_scope` | 连公开也要写 `["*"]`。代码里没有默认值可填：判断归本体，不归代码 |
| `dimension_policy` | 用 `{"mode": "all\|only\|except"}` 一个字段，取代 `allowed_` / `forbidden_` 两个。那两个字段的空数组语义是反的（一个空表示不限、一个空表示全禁），一定会被写错 |
| `supports_compare` | 不看本体级声明，看指标自己 |

`version` 不是 `"1.1"` 直接拒绝，不做向后兼容——兼容层会掩盖"两边看到的本体不一样"这件事。

本模块**只校验它自己读的字段**。`expr` 里的列名、`{f}.` 前缀、`grain` 是否齐备——归 `mql-compiler`。它照样能把切片服务出来，因为切片回答的是"结构层面能写什么"，不是"能不能编译通过"。

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 自述：版本、域名、指标数、端点 |
| `GET` | `/health` | 健康检查 |
| `POST` | `/slice` | 请求体见下，成功 200，错误 400 |

```json
{
  "metric_terms": ["成交额"],
  "dim_terms": ["城市"],
  "domain": "ord",
  "principal": {"subject": "role:region_manager"},
  "max_metrics": 8,
  "max_properties": 30,
  "include_relations": true
}
```

响应里除了 `view`，还有四组匹配结果和一份截断清单：

```json
{
  "ok": true,
  "ontology_version": "1.1",
  "view": {"capabilities": {}, "metrics": [], "properties": [], "relations": [], "glossary": []},
  "matched":   [{"term": "城市", "kind": "property", "name": "city", "how": "alias"}],
  "ambiguous": [{"term": "成交额", "kind": "metric", "candidates": ["gmv", "gmv_settle"],
                 "reason": "family_no_default"}],
  "unmatched": [{"term": "毛利", "kind": "metric", "hint": "本体无此词"}],
  "conflicts": [{"property": "store", "metric": "same_store_gmv",
                 "reason": "dimension_forbidden", "policy": {"mode": "only", "dimensions": ["city", "region"]}}],
  "truncated": {"metrics": [], "properties": []},
  "view_hash": "sha256:..."
}
```

`truncated` 显式列出被砍掉的名字，不是可选项——调用方需要知道"模型没看到什么"，否则它无法解释这次回答为什么偏了。

错误一律是对象，**不许是 `list[str]`**：

```json
{"ok": false,
 "error": {"code": "UNKNOWN_DOMAIN", "path": "request.domain",
           "message": "本体没有声明域 'nope'", "candidates": ["mkp", "ord", "settle"]}}
```

六个错误码：`BAD_REQUEST` / `UNKNOWN_PARAM` / `BAD_LIMIT` / `BAD_PRINCIPAL` / `UNKNOWN_DOMAIN` / `BAD_ONTOLOGY`。

---

## 装与测

```bash
pip install -e ".[dev]"
pytest -q
# 116 passed
```

不用装也能跑 CLI——包是纯标准库的，`python -m ontology_slicer` 直接可用。

---

## 相关

- [`mql-spec`](https://github.com/Aiops-seal/mql-spec)：MQL 的语言定义与参考校验器。切片产出的一切，最终要被它接受
- [`mql-compiler`](https://github.com/Aiops-seal/mql-compiler)：MQL + **完整本体** → SQL + plan。注意它拿的永远不是切片
- `SPEC.md`：完整的规范，589 行。上面没讲到的边界都在那儿

## License

MIT
