# 零位区间交班

现有 README 说明双轨导入、冻结、发件、隔离实收、签报和领取。这里约定新增区间业务的调用和样例。

## 发件和实收

用 `issue.register.issue_packet(store, revision_id, public_ids, version=2)` 申请区间件。返回的仍是 `(packet_bytes, IssueRegistration)`。省略 `version` 时维持原 V1 行为。发行只接受指定冻结版及原规则下的公开拍，发行时无需区间和锚票。

V2 沿用封套的 `issue_id`、`sha256` 和 Base64 `body` 字段，外层 `version` 为整数 2。正文固定 `run_id`、`revision_id`、只含公开主拍号且按原序排列的 `main` 目录、全部副拍号 `secondary` 目录、主轨拍数 `main_count`、公开主拍原轨位置 `public_positions` 和派生弧 `arcs`。每条弧是 `[主拍原序位置, 副拍原序位置, 整数读数差]`，读数差为 `主读数 - 副读数`；弧清单即离线可行性的唯一描述，采样窗宽不外发。隐去主拍的拍号、两轨采样序、原始读数、逐拍候选清单和源站逐拍见证留站。空披露时 `main` 和 `public_positions` 均为空列表，弧仍覆盖全部主拍位置。

仍用 `intake.receive.receive_packet(receipts, packet_bytes, registration)` 实收。根据原登记校验实际正文字节和身份，并校验该版正文结构、证据数据及公开引用。仅改外层版本号不能让正文通过另一版本的验件。错件以 `ValueError` 拒绝，不新增实收。

## 签报和领取

用 `review.publish.sign_interval_review(receipts, reports, receipt_id, lower, upper, anchors=())` 签存。两个端点接受 Python `int`（不含 `bool`）或 `fractions.Fraction`，相等时为单点查询。锚票沿用原公开拍号规则和顺序归一规则。锚票语法和身份均有效但共同占位冲突时，仍属合法查询。

在每个修正量 `t` 下，每个主拍必须匹配一个原采样窗内副拍，副拍在整条路径上严格递增。读差是 `abs(主拍读数 - (副拍读数 + t))`，每拍恰计一次，锚票约束同一条完整路径。比较闭区间内逐点最低总差，取其中最大值及达到它的最小 `t`。在该点的最优完整路径中，取公开主拍原序下副拍位置字典序最小的公开配对。

返回报告可读属性 `report_id`、`receipt_id`、`issue_id`、`run_id`、`revision_id`、`body_sha256`、`anchors`、`interval`、`total_cost`、`correction`、`pairs`。`interval` 是两个 `Fraction` 的元组，有解时 `total_cost` 和 `correction` 也为 `Fraction`。`pairs` 是原拍号二元组构成的元组。空披露且有解时 `pairs` 为 `()`。空主轨的空路径总差为 0，取区间下端。合法无解时仍有报告，`total_cost`、`correction`、`pairs` 均为 `None`。

区间入口遇 V1 实收件、非法端点、逆序区间或非法锚票时抛 `ValueError`，未知实收号抛 `LookupError`。拒绝和写入失败均无新增报告。沿用 `review.retrieve.get_report(reports, report_id)` 领取已签报告。重开领取返回原区间、原锚、原结果及指定实收件的登记身份，不需要源库或再次核算。原 V1 件及报告仍按旧接口读写。

## 用样例核对交班

`samples/zero_interval.json` 的每项是一份录制。将 `run_id`、`main`、`secondary` 和 `window` 交给原导入入口，冻结后用 `public` 发件。每条 `queries` 中的区间端点和预期数值以字符串保存，读样例时用 `Fraction` 转成 Python 数值。`anchors` 是签报时才交的票，`pairs` 是公开配对的预期。隐去 H 的两份样例分别用独立源库发行。现有 `windowed_run` 也可验证双锚，在 `[0,1]` 用 A→2 和 B→4 应签总差 8、修正量 1，移除副拍 3 的勘误版应签无解。

可沿用原回归的 `ArchiveCase.source()` 和 `ArchiveCase.night()` 在两个库里走交班，退出源库上下文后再实收。签存后重开隔离库领取。需要核对提交拒绝时，原回归的 `reject_commit` 可用于隔离库连接。`python3 -B check_interval_samples.py` 用现有 V1 路径核对新增样例的固定零位结果；`python3 -B check_interval_v2.py` 用 V2 路径核对同样样例的区间裁决、`windowed_run` 双锚与无解、合法无解签存、关源后重开领取，以及全部拒绝规则。`tests/test_interval.py` 另含 120 组随机参数，用独立的全路径枚举精确 oracle 比对分段线性求解器。

保留 `.pointwise-runtime.json`。现有清单为 `version=1`、`kind=cli`，`setup` 为空，`verify` 和 `demo` 各是一条命令的参数数组。`verify` 入口 `check_project.py` 先执行原 unittest 回归（含 V2 用例），通过后再执行 V2 区间交班的业务核验，失败以非零状态退出。`demo` 输出实际实收和签报结果，在源库连接关闭后完成 V2 关源签报，并重开隔离库按报告号领取区间、锚票、裁决及实收出处。
