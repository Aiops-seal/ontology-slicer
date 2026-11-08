# ontology-slicer 规范 v1.0

> 本体切片器。输入一份完整本体、一组意图词、一个调用主体，输出一块能塞进模型上下文的本体切片。
>
> **它不做判断，只做装配。** 所有"能做什么、不能做什么"都由本体声明，本模块原样搬运。

---

## 0. 一句话与边界

```
slice(ontology, intent_params, principal) → view
```

**全集 ∩ 相关 ∩ 可见。**

- `ontology` 提供全集
- `intent_params` 收窄"相关"
- `principal` 标注"可见"

**是：** 词到结构的精确对齐、依赖闭包装配、能力声明的搬运、切片指纹计算、一个无状态 HTTP 服务。

**不是：** 不是语义检索引擎（不接 embedding、不做同义词扩展）、不是元数据采集器（不查 `information_schema`）、不是权限系统（不鉴权，只按声明标注）、不是编译器（不判断能不能连上、不出 SQL）、不是本体构建工具（那是离线的另一个子系统）。

---

## 1. 它为什么存在

三条判断，写在这里是因为它们决定了后面每一个设计取舍。

### 1.1 它的价值不是省 token

省 token 是副产物。真正的作用是**把模型的候选空间缩小到"只可能写对"的范围**——模型看不见的东西，它写不出来。

这跟 LLM 无关、跟 embedding 无关，是一道结构闸门。

### 1.2 切片只给模型看，编译器永远拿完整本体

`mqlc.compile(mql, ontology, ctx)` 里的 `ontology` 永远是完整的那份，不是切片。

一旦有人把切片当数据源递给编译器，权限、截断、hash 三件事会互相污染，而且再也没有人知道编译器到底看到了什么。

### 1.3 判断归本体，不归代码

"成交额有三个口径，该不该追问用户"——这个问题的答案写在本体的 `ambiguity_group` + `default_in_group` 里，不在本模块的某个参数里。

**有旋钮的地方就一定会被调，调了以后同一份本体在不同部署里的行为不一样。** 所以本模块没有任何行为开关：没有 `strict=False`、没有 `guess=True`、没有 `default_time`。

---

## 2. 与 mql-compiler 的关系

**同一份本体文件，两个读者，零代码共享。**

| | `mqlc` 读什么 | `ontology_slicer` 读什么 |
|---|---|---|
| 表 | `kind` / `layer` / `grain` / `columns` 全部 | 只要 `name`（做闭包定位） |
| 关系 | `on` 的两列、`cardinality` | `from` / `to` / `cardinality` |
| 指标 | `bindings[].expr` 的合法性、`scoped_filters` | `domain` / `aliases` / `visibility_scope` / `dimension_policy` / `supports_compare` / `do_not` / `ambiguity_group` |
| 属性 | `bindings[].column` 是否存在 | `domain` / `aliases` / 绑在哪张表 |

**推论：本模块校验的范围，只到它自己读的字段（见 §3.5）。** 物理层的错（`expr` 里列名不存在、`{f}.` 前缀漏了）归 `mqlc` 报。本模块照样能把切片服务出来——因为切片是"结构层面能写什么"，不是"能不能编译通过"。

这条边界如果破了，本模块就成了半个编译器。

---

## 3. 本体格式 `v1.1`

在 `mql-compiler` 的 `v1.0` 上**只加字段，不改已有字段**。`mqlc` 的加载器忽略不认识的键，所以扩展这份文件不需要改 `mqlc` 一行代码。

### 3.1 顶层新增

```json
{
  "version": "1.1",
  "domain": "ord",
  "capabilities": {
    "supports_compare": ["yoy", "mom"],
    "min_granularity": "day",
    "max_scan_days": 1800,
    "dialects_verified": ["sqlite"]
  },
  "glossary": [
    {"term": "销售业绩", "maps_to": "metric:gmv"}
  ],
  "tables": [],
  "relations": [],
  "metrics": [],
  "properties": []
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `version` | 是 | 本模块要求 `"1.1"`。读到 `"1.0"` 直接 `BAD_ONTOLOGY`，不做兼容猜测 |
| `capabilities` | 是 | 本体级能力声明，原样搬进 `view.capabilities` |
| `capabilities.supports_compare` | 是 | 枚举值来自 `yoy/mom/wow`，可空数组 |
| `capabilities.min_granularity` | 是 | `day/week/month/quarter/year`。比它更细的粒度不该被写出来 |
| `capabilities.max_scan_days` | 是 | 正整数。查询跨度上限 |
| `capabilities.dialects_verified` | 是 | 真跑过的方言。**只写跑过的**，没验证不许写 |
| `glossary` | 否 | 业务黑话到正式名的映射，`maps_to` 形如 `metric:gmv` / `property:city` |

### 3.2 指标级新增

```json
{
  "name": "gmv",
  "display": "成交额",
  "domain": "ord",
  "aliases": ["成交额", "销售额", "GMV", "jiaoyie"],
  "do_not": ["含退款，不要与 gmv_settle 混用"],
  "visibility_scope": ["*"],
  "dimension_policy": {"mode": "except", "dimensions": ["store"]},
  "supports_compare": true,
  "ambiguity_group": "gmv_family",
  "default_in_group": false,
  "bindings": [],
  "scoped_filters": []
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `domain` | **是** | 业务域。缺域就无法消歧，所以不给默认值 |
| `aliases` | **是** | 可空数组。**必填而不是可选**，是为了让"这个指标没有别名"成为一个明确的声明，而不是一个省略 |
| `do_not` | 否 | 明确禁止的用法，原样进 view。这是最容易丢、又最该让模型看见的东西 |
| `visibility_scope` | **是** | 角色列表，`"*"` 表示全员。**连"公开"也要写出来**，代码里没有默认值可填 |
| `dimension_policy` | **是** | 见 §3.4 |
| `supports_compare` | **是** | 布尔。不看本体级声明，看指标自己 |
| `ambiguity_group` | 否 | 口径族名。同族指标互为候选 |
| `default_in_group` | 否 | 布尔。仅在 `ambiguity_group` 存在时有意义 |

### 3.3 属性级新增

```json
{
  "name": "city",
  "display": "城市",
  "kind": "enum",
  "domain": "ord",
  "aliases": ["城市", "城市名"],
  "do_not": [],
  "bindings": []
}
```

`domain` 与 `aliases` 必填，理由同指标。属性**不设** `visibility_scope`——理由见 §10。

### 3.4 `dimension_policy`

取代原来的 `allowed_dimensions` / `forbidden_dimensions` 两个字段。两个字段的空数组语义是反的（一个空表示不限、一个空表示全禁），一定会被写错。

```json
{"mode": "all"}
{"mode": "only",   "dimensions": ["city", "region"]}
{"mode": "except", "dimensions": ["store"]}
```

| `mode` | 语义 |
|---|---|
| `all` | 不限制。`dimensions` 必须缺省或为空，否则 `BAD_ONTOLOGY` |
| `only` | 只能按列出的维度拆 |
| `except` | 除列出的以外都能拆 |

### 3.5 本模块的校验范围

**只校验它读的字段。** 具体是：

| 会校验 | 不校验（归 `mqlc`） |
|---|---|
| 顶层缺 `version` / `capabilities` / `tables` / `metrics` / `properties` | `bindings[].expr` 里的 SQL 是否合法 |
| `version != "1.1"` | `{f}.` 前缀是否漏了 |
| 表 `name` 重复、关系两端不存在 | `grain` 里的列是否在 `columns` 里 |
| 指标缺 `domain` / `aliases` / `visibility_scope` / `dimension_policy` / `supports_compare` | `scoped_filters[].condition` 里引用的表是否越界 |
| `dimension_policy.mode` 非法，或 `all` 带了 `dimensions` | 属性 `bindings[].column` 是否存在 |
| 同组多个 `default_in_group: true` | `layer` / `grain` 是否齐备 |
| `bindings[].table` 指向不存在的表（闭包要用） | — |

**校验在建索引时一次做完，失败即拒绝启动。** 服务不接一个半坏的本体。

---

## 4. 请求

### 4.1 结构

```json
{
  "metric_terms": ["成交额", "GMV"],
  "dim_terms": ["城市"],
  "domain": "ord",
  "principal": {"subject": "role:region_manager"},
  "max_metrics": 8,
  "max_properties": 30,
  "include_relations": true
}
```

### 4.2 字段

| 字段 | 必填 | 缺省 | 说明 |
|---|---|---|---|
| `metric_terms` | 是（可空数组） | — | **原始词，未归一**。空数组合法：允许"只问属性"的请求 |
| `dim_terms` | 否 | `[]` | 维度意向词 |
| `domain` | 否 | `null` | 域提示。**不传就是"我不确定"，正确输出是让它去问，不是替它挑一个** |
| `principal` | 否 | `null` | 见 §4.4 |
| `max_metrics` | 否 | `8` | 正整数，上限 `50` |
| `max_properties` | 否 | `30` | 正整数，上限 `200` |
| `include_relations` | 否 | `true` | 是否把指标所在事实表的直接邻接维表一起带出来 |

**没有 `filter_terms`。** 曾经的契约里有，已删除：`filter_terms` 里装的是**值**（"华东"、"昨天"），值域在数据层不在结构层。本模块手里只有本体，永远无法判断"华东"是不是合法值，最多能猜"本体里有个叫 region 的属性"——而拿值去猜维度，会把一堆不相关的维度塞进 view，直接撞穿 `max_properties`。收益小、风险大。

**没有 `max_hops`。** "指标到表"是一跳（`binding.table`），"表到维表"再一跳。跳数恒为二，做成参数只会被误用。想要的效果用 `include_relations` 一个布尔表达。

### 4.3 严格模式

出现未定义的请求字段 → `UNKNOWN_PARAM`，附合法字段列表。

不做"忽略未知字段"的宽容模式。请求里的每个字段都该有人负责，多出来的说明调用方以为它有用。

### 4.4 `principal`

```json
{"subject": "role:region_manager"}
```

**`subject` 必须以 `role:` 开头，否则 `BAD_PRINCIPAL`。**

这条看起来很硬，但它挡的是三样东西被揉成一团：

| 是什么 | 例子 | 归谁 |
|---|---|---|
| 人（身份） | `zhangsan` | 网关。有会话、有 SSO |
| 角色（指标可见性） | `role:region_manager` | **本模块** |
| 数据行范围（行级权限） | `region_ids: ["R01"]` | `mqlc` 的 `permissions` |

一旦有人把 `region_ids` 递进来，本模块就得开始想"要不要用它过滤"——那时候它已经在往编译器的地盘上越界，而它根本不知道 `region` 挂在哪张表上。

**人 → 角色由网关解析，角色 → 指标由本模块查声明，角色 → 数据行由编译器转 WHERE。** 三方各管一段，中间不互相借条件。

---

## 5. 响应

### 5.1 结构

```json
{
  "ok": true,
  "ontology_version": "1.1",
  "view": {
    "capabilities": {},
    "metrics": [],
    "properties": [],
    "relations": [],
    "glossary": []
  },
  "matched": [],
  "ambiguous": [],
  "unmatched": [],
  "conflicts": [],
  "truncated": {"metrics": [], "properties": []},
  "view_hash": "sha256:..."
}
```

### 5.2 `view.metrics[]` 里每个指标

```json
{
  "name": "gmv",
  "display": "成交额",
  "domain": "ord",
  "aliases": ["成交额", "销售额", "GMV", "jiaoyie"],
  "formula": "SUM({f}.pay_amount)",
  "do_not": ["含退款，不要与 gmv_settle 混用"],
  "dimension_policy": {"mode": "except", "dimensions": ["store"]},
  "supports_compare": true,
  "ambiguity_group": "gmv_family",
  "default_in_group": false,
  "access": "granted"
}
```

几点必须说明：

- **`formula` 给模型看，但不给出口。** 理解口径靠它，抄进 MQL 由 `mql-spec` 的 `PHYSICAL_LEAK` 拦。理解与出口是两把锁，不是一把。多个 binding 时取第一个 binding 的 `expr`，字段名固定为 `formula`（不暴露 `bindings` 这个结构）。
- **`access` 是唯一随 `principal` 变的字段**，且不参与 `view_hash`（§11）。
- 没有 `bindings` / 表名 / 列名。模型不需要知道物理位置，知道了只会想写进去。

### 5.3 四组匹配结果

```json
"matched": [
  {"term": "GMV", "kind": "metric", "name": "gmv", "how": "alias"}
],
"ambiguous": [
  {"term": "成交额", "kind": "metric",
   "candidates": ["gmv", "gmv_settle"], "reason": "family_no_default"}
],
"unmatched": [
  {"term": "毛利", "kind": "metric", "hint": "本体无此词"}
],
"conflicts": [
  {"term": "门店", "metric": "gmv",
   "reason": "dimension_forbidden", "policy": "except"}
]
```

| 组 | 含义 |
|---|---|
| `matched` | 唯一确定。`how` 取 `name` / `alias` / `glossary` / `default` |
| `ambiguous` | **多个口径都成立**，必须由上游追问用户 |
| `unmatched` | 本体里没有。**绝不在本模块内被"语义推断"补上** |
| `conflicts` | 用户要的维度和指标的 `dimension_policy` 冲突。不静默丢弃，单独报出来 |

`hint` **必须保持机械**，只有四种取值：

- `本体无此词`
- `该词只出现在域 {domain}`
- `该词指向{指标|属性}，位置不对`
- ~~`有 {n} 个近似词，需人工确认`~~ —— **删掉了**。本模块不做模糊匹配，这个 hint 永远产不出来；写一个产不出来的分支，等于留一句假承诺

`conflicts` 是四组里唯一"额外"的，加它的理由是：信息一旦被静默丢弃，上游就永远不知道为什么切片里少了那个维度。

### 5.4 `truncated`

超出 `max_metrics` / `max_properties` 被砍掉的名字，按 §9 的优先序排列。

**显式列出来，不是可选项。** 调用方需要知道"模型没看到什么"，否则它无法判断这次回答为什么偏了。

---

## 6. 流程

四步，全确定性，零 LLM：

```
① 匹配   词 → 指标/属性 + 三态结果
② 闭包   选中指标 → 事实表 → 邻接维表 → 属性
③ 截断   按优先序砍到上限内，记录被砍的
④ 指纹   去掉 access 后规范化 JSON → sha256
```

权限标注发生在 ③ 之后、④ 之前，且只影响 `access` 字段。

---

## 7. 匹配

### 7.1 归一

只做两件事：`strip()`，ASCII 转小写。

**不做**全半角转换、不做繁简转换、不做去空格连字符。那些都是"猜"，一旦做了就无法解释为什么 `ＧＭＶ` 能对上而 `GMV ` 对不上别的。

### 7.2 候选怎么取

每个词独立走一遍，两个阶段。

**第一阶段——glossary 优先。**

`glossary[].term` 精确命中就直接取 `maps_to` 指向的目标，`how = "glossary"`。**不再走 glossary，防环。**

**第二阶段——名字、display、别名合并取候选，不是命中即停。**

| 词出现在 | 取哪些候选 | `how` |
|---|---|---|
| `metric_terms` | `metric.name` ∪ `metric.display` ∪ `metric.alias` | 名字有命中 `name`；否则 display 有命中 `display`；否则 `alias` |
| `dim_terms` | `property.name` ∪ `property.display` ∪ `property.alias` | 同上 |

`how` 只报**优先级最高那一层**：一个词同时出现在三层时，报 `name` → `display` → `alias`。候选集才是三层并集。

**display 为什么必须查。** 它和 `name`、`alias` 一样是本体写下的字段，人和看板都用它。脏本体里 display 常常没写进别名表——`ord_qty` 的 display 是「商品件数」，别名只收了「件数 / 销量 / ord_qty」。不查 display，用户最正式的那个问法会被判成 `unmatched`，而它明明就写在本体第一行。

这不算「猜」：猜指的是近义扩展、拼音纠错、embedding 找最像。查一个本体已经声明的字段，是搬运。

合并而不是分层命中即停，是踩出来的：

`gmv` 既是 `gmv` 这个指标的名字，又被 `gmv` 和 `gmv_settle` 同时声明成别名。按「名字层优先」，这个词会被静默判给 `gmv` —— 可两个指标都声称自己叫这个。既然都声称，这个词就是有歧义的，正确输出是让上游去问，不是按层级悄悄选一个。

**名字层能主导的只有一种情况：这个词是某个指标的名字，且没有别的指标把它用作别名。**

`metric_terms` 只在指标里找，`dim_terms` 只在属性里找。一个词出现在错的位置上，算 `unmatched` 并在 hint 里说明。

### 7.3 歧义判定

候选集 = 该层级命中的全部目标。判定顺序固定：

```
候选为空              → 落到下一层；全部落空 → unmatched
候选 1 个             → matched
候选 ≥2 且同属一组：
    组内恰有 1 个 default → matched, how="default"，该组全部成员进 view
    组内 0 个 default     → ambiguous, reason="family_no_default"
候选 ≥2 且不属同组     → ambiguous, reason="multi_hit"
```

**`view` 必须包含 `ambiguous` 的全部候选指标。** 少了任何一个，上游都没法把澄清问题问完整——问"你要哪个成交额"却只给模型看了一个成交额，等于没问。

### 7.4 域过滤

发生在候选集算出之后、歧义判定之前。

```
domain 非空 → candidates = [c for c in candidates if c.domain == domain]
```

过滤后：

| 结果 | 输出 |
|---|---|
| 空 | `unmatched`，hint = `该词只出现在域 settle`（列出实际所在域） |
| 1 个 | `matched` |
| ≥2 个 | 继续走 §7.3 的歧义判定 |

**不传 `domain` 时，跨域同名指标必须进 `ambiguous`。** 这是"不猜"的直接体现：调用方没说清是哪个域，正确输出是让它去问，不是挑一个看起来更像的。

`domain` 必须在 `capabilities` 之外单独存在于至少一个指标的 `domain` 上，否则 `UNKNOWN_DOMAIN`。

### 7.5 组默认与"诚实度"

同一个 `ambiguity_group` 里，`default_in_group: true` 的存在与否，决定了本模块这次是"直接给"还是"必须问"。

**这是本体在决定，不是代码在决定。** 代码里没有 `allow_default` 这个开关。

---

## 8. 闭包

起点：已选指标（`matched` 的 + `ambiguous` 的全部候选 + 同组兄弟的并集）。

```
F  = ∪{m.bindings[].table} ∪ {scoped_filters[].table}     事实侧
D  = ∪{neighbors(f) 的 dst, f ∈ F}                          维表侧（include_relations=true 时）
T  = F ∪ D
```

规则：

1. **只在 `include_relations=true` 时展开 D。** 展开只走一跳，不递归。
2. **一个指标有几个 `binding`，几张表就都进闭包。** 切片器不选表——选表是编译器按成本、权限、粒度做的判断。切片只负责把「可能用到的结构」全给出来，包括那条 `one_to_many` 的预聚合路径。少给一条，模型就以为那条路不存在。
3. `neighbors` 不分方向、不看 `cardinality`——**连得上不代表连得对，那是编译器的判断**。`cardinality` 原样带进 `view.relations`，让下游自己看扇出风险。
4. `view.relations` = 两端都在 `T` 内、且至少一端在 `F` 的边。方向保持本体里的原始方向，不反转、不补逆边。
5. 属性候选 = `bindings[].table ∈ T` 的全部属性。
6. `glossary` 只带**命中本次请求的词**对应的条目，不全量搬。

**闭包不判断跨域可连性。** "这两个域之间有没有 JOIN 路径"属于 `mqlc` 的 `NO_CANDIDATE_TABLE`。本模块报这类错的那一刻，就等于偷偷实现了一半编译器。

---

## 9. 截断

### 9.1 指标优先序（高 → 低）

| 序 | 类别 |
|---|---|
| 1 | `matched` 的指标 |
| 2 | `ambiguous` 的候选 |
| 3 | 同 `ambiguity_group` 且**同域**的兄弟 |

**第 1、2 序永不截断。** 否则会出现最荒诞的一种结果：用户问 GMV，返回的 view 里没有 GMV。

**第 3 序必须同域。** 否则 `domain=ord` 的请求会被兄弟关系绕过，把 `settle` 域的指标塞回来——那样「域收窄」只对 `matched` 生效，对同组失效，等于没收窄。这条是实测踩出来的：`domain=ord` 查「成交额」，`gmv_settle` 照样出现在 view 里。

同序内部按 `name` 升序，保证同一请求两次结果一致。

### 9.2 属性优先序

| 序 | 类别 |
|---|---|
| 1 | `dim_terms` 命中的 |
| 2 | 绑在与已选指标同一张表上的 |
| 3 | 绑在维表上的 |
| 4 | 其余，按 `name` 升序 |

### 9.3 记录

被砍的进 `truncated.metrics` / `truncated.properties`，按同样的优先序排列。截断本身不报错——超限是正常情况，但沉默地砍是事故。

---

## 10. 权限标注

```
principal 缺省                    → access = "unknown"
"*" ∈ visibility_scope            → "granted"
principal.subject ∈ scope         → "granted"
否则                              → "denied"
```

四条硬规则：

1. **缺省是 `unknown`，不是 `granted`。** 默认放行比默认拒绝危险得多。
2. **被 `denied` 的指标仍然出现在 `view` 里。** 指标名通常不是秘密，数据才是；藏掉只会让模型说"不知道"，用户听到的是系统坏了。
3. **本模块不鉴权。** 它信任传进来的 `subject`，就像 `mqlc` 信任传进来的 `permissions`。谁配得上这个角色，是网关的事。同一原则：**不判定，只转换。**
4. **属性不标 `access`。** 属性级权限会让 `view` 的形状随角色变化，`view_hash` 就不再稳定，回归测试也失去意义。

---

## 11. `view_hash`

### 11.1 定义

```python
canonical = json.dumps(strip_access(view), sort_keys=True,
                       ensure_ascii=False, separators=(",", ":"))
view_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

`strip_access` 递归删除所有 `access` 键。

### 11.2 它是什么，不是什么

**是**：`(ontology 内容, intent_params)` 的函数。同一组词、同一份本体 → 同一个 hash。

**不是**：

- 不是缓存键（本模块无状态、不缓存）
- 不是 plan 里的字段。`mqlc.compile(mql, ontology, ctx)` 拿不到 hash；塞进 MQL 会破"只停在业务语义层"，塞进 ctx 只是装饰。**plan 需要的是 `ontology_version`——hash 是它的可复算指纹，不是要传递的凭据。**
- 不代表"同一份可执行的答案"。同一份 view，不同角色 `access` 不同。

hash 的用处在 `mql-author` 的 `evidence` 里：「当时模型看到的切片，是这一份」。

---

## 12. 错误码

```
BAD_REQUEST       顶层不是对象 / metric_terms 缺失或类型不符
UNKNOWN_PARAM     请求里出现未定义的字段（严格模式）
BAD_LIMIT         max_metrics / max_properties 非正整数或超出上限
BAD_PRINCIPAL     principal 不是 {"subject": "role:..."} 形态
UNKNOWN_DOMAIN    domain 不在本体声明的域里
BAD_ONTOLOGY      本体结构非法 / 版本不是 1.1 / 同组多个默认
```

统一形状：

```json
{"ok": false,
 "error": {"code": "UNKNOWN_DOMAIN", "path": "request.domain",
           "message": "...", "candidates": ["ord", "settle", "mkp"]}}
```

**不许返回 `list[str]`。** 调用方要按 `code` 决定是"改请求重试"还是"停下来报错"，字符串匹配不可靠。

错误码止步于此：**不设 JOIN 类、编译类错误码。** 那是 `mqlc` 的地盘。

---

## 13. HTTP 接口

无状态，标准库 `http.server`，零依赖。

```
python -m ontology_slicer serve --ontology fixtures/ontology.json --port 8765
```

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 自述：name、version、ontology_version、domains、endpoint |
| `GET` | `/health` | `{"ok": true, "ontology_version": "1.1", "metrics": 12, "properties": 10}` |
| `POST` | `/slice` | 请求体为 §4 结构，成功 `200`，失败 `400`（`BAD_REQUEST`/`UNKNOWN_PARAM`/`BAD_LIMIT`/`BAD_PRINCIPAL`/`UNKNOWN_DOMAIN`），本体问题在启动时就已拒绝，不会走到这里 |

- `Content-Type: application/json; charset=utf-8`
- 响应体一律 JSON，成功 `{"ok": true, ...}`，失败 `{"ok": false, "error": {...}}`
- **不设鉴权**，不读 header。要鉴权就在前面放网关
- **不缓存在进程外**，不写日志到文件
- 本体在启动时加载一次，改本体重启。不做热更新——热更新会让同一进程在不同时刻给出不同 hash，那比不可用更难查

---

## 14. 能力上限（写进 README）

| 不做 | 因为 |
|---|---|
| 语义相似 / 同义词扩展 / embedding | 精确匹配敢说"这三个都对，你去问用户"；检索只会还 Top-K 让你随便挑，挑错了也没人知道 |
| 值域校验 | 不知道"华东"是不是合法值。值域在数据层 |
| 跨域 JOIN 可行性 | `NO_CANDIDATE_TABLE` 是编译器的错 |
| 排序 / 打相关性分数 | 截断按固定优先序，不按"像不像" |
| 自动补 `unmatched` | 一旦开始猜，确定性论证当场失效 |
| 鉴权 | 网关的事 |
| 缓存 / 热更新 | 会让同一份本体在时间维度上不一致 |

**代码量会很小，这是设计结果，不是没做完。** 薄是因为判断都在本体里、在离线的本体构建阶段，不在这儿。

---

## 15. 版本策略

- `view` 的结构一旦被外部用于写 prompt 或 yaml，即为**公开接口**，改动需升 major
- 本体 `version` 不是 `"1.1"` 直接拒绝，不做向后兼容——兼容层会掩盖"两边看到的本体不一样"这件事
- 本模块不声明对 `mqlc` 的兼容版本，只声明本体格式版本
