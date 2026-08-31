# ADR-0006：论文族级四区数据协议

- 状态：Accepted
- 日期：2026-08-18
- 最近核验：2026-08-31

## 背景

Corpus 构建、Memory Program 进化和 Official 评价需要清晰的数据职责。论文修订版、标题近似项和同族记录必须落入同一区域。

## 决策

1. 先按 paper family 聚合，再按时间规则形成 A、B、C、D 四区。
2. A 是 historical source：Core v1 从中选择 125 篇，并按五角色构建 625 条 no-memory Full Review。
3. B 是 evolution validation：每个角色从 B-local view 获得 static-25、rotating schedule 和 full pool。
4. C 是 protected Official：Final Review Suite 冻结后才授权访问 C 与对应 Human/CoCo 参照。
5. D 是 legacy benchmark reserved：它服务旧 CoCo 路径，并保持与 Core v1 Official 输入分离。
6. 每个 split 物化 paper IDs、family IDs、生成规则、计数和内容校验值。

当前 v2 zone 数据的实体计数为 A=2391、B=299、C=300、D=899。配置与 manifest 的当前完成度记录在系统状态文档。

## 结果

Corpus、Evolution 和 Official 的数据职责形成单向流程。Family-level 分组约束控制论文版本泄漏，受保护访问 seam 控制参照进入时点。

## 替代方案

逐记录随机切分会把同族版本分散到多个区域。把 D 作为 Core Official 会混合 legacy benchmark 口径。当前四区职责为两条运行路径提供明确隔离。
