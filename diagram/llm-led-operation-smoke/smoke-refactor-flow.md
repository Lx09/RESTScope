# LLM 主导的 Operation Smoke 重构流程

本文描述重构后的 Operation Smoke 运行逻辑。阅读顺序：

1. 先看“总流程”，理解外层 Batch、固定 Todo、候选 Patch 和下一轮之间的关系。
2. 再看 Plan、Solve、Patch、Effect 四个 Agent 的协议图。
3. 最后查阅请求字段、预算、终止状态和 App 生命周期状态表。

## 1. 总流程

```mermaid
flowchart TD
    START([收到 OperationSmokeRequest])
    LOAD[读取 operation 与 testing 配置]
    LOAD_RESULT{读取结果}
    DB_ERROR([数据库异常<br/>上抛为 Supervisor 全局技术错误])
    OP_ERROR([OperationSmokeResult<br/>status = errored<br/>stop_reason = operation_error])
    RECOVER[恢复可能遗留的 Catalog candidate]
    ENABLED{Operation Smoke<br/>已启用且受支持?}
    UNSUPPORTED([OperationSmokeResult<br/>status = unsupported<br/>stop_reason = unsupported_operation])
    INIT[初始化本次 Smoke run<br/>固定 seed<br/>加载 operation 隔离历史<br/>加载已接受 runtime Constraints]
    REF_POOL{配置要求的 reference pool<br/>均非空?}
    FULL_BATCH[运行当前已接受配置的完整 Batch<br/>固定 seed + case_count<br/>完整 preflight / serialization / HTTP / Behavior Monitor]
    REVISION_OK{执行报告使用的<br/>Catalog revision 正确?}
    RATE{最新完整 Batch 的<br/>2xx success rate<br/>>= threshold?}
    PASSED([OperationSmokeResult<br/>status = passed])
    PLAN_BUDGET{全局 Plan 输出预算<br/>仍有剩余?}
    PLAN_EXHAUSTED([OperationSmokeResult<br/>status = retry<br/>stop_reason = plan_budget_exhausted])
    RECORD_BATCH[把完整 Batch、failure/case 快照<br/>写入 operation 隔离 App 内存账本]
    PLAN[[P. 新建 smoke_plan THINK 会话<br/>输入完整 Batch + 临时代号 + 历史]]
    PLAN_RESULT{Plan 结果}
    NO_WORK([OperationSmokeResult<br/>status = retry<br/>stop_reason = no_new_failure_work])
    EXPAND[验证代号引用与失败 case 覆盖<br/>将 Todo 中的 case code 立即展开为完整证据<br/>固定本轮 Todo 顺序]
    TODO_LEFT{本轮固定 Todo<br/>还有未处理项?}
    ROUND_DONE[记录 Round summary<br/>本轮之后出现的新 failure<br/>留给下一轮 Plan]
    SELECT_TODO[选择下一个 Todo<br/>读取最新已接受 Batch<br/>重新计算可用 references]
    SOLVE[[S. 为该 Todo 新建 failure_solver THINK Agent<br/>Todo 内保持连续会话]]
    SOLVE_RESULT{Solve 输出}
    TODO_FINISH[记录 Todo 终止状态<br/>already_absent / non_parameter / dependency_related<br/>insufficient_evidence / no_new_attempt / solve_budget_exhausted]
    PATCH_REQ[/PatchRequirement<br/>root_cause<br/>affected_inputs<br/>desired_behavior<br/>acceptance_criteria/]
    PATCH[[A. 新建 parameter_patch FAST Agent]]
    PATCH_RESULT{Patch Agent 结果}
    PATCH_FAILED[记录 Patch 失败与完整尝试历史]
    PREPARE[准备候选修改<br/>扩展 generator presence closure<br/>组合候选 runtime Constraints]
    HAS_GENERATOR{包含 Generator 修改?}
    STAGE[stage Catalog candidate revision]
    NO_STAGE[仅在内存中应用候选 Constraints]
    CANDIDATE_BATCH[运行完整 candidate Batch<br/>与 before Batch 使用相同 seed / case_count]
    CANDIDATE_REV{candidate report 的<br/>revision 正确?}
    EFFECT[[E. 新建 smoke_effect THINK Agent<br/>比较 before Batch 与 candidate Batch]]
    EFFECT_RESULT{Effect 结论}
    ACCEPT[原子接受全部候选<br/>finalize Generator candidate<br/>加入 App-lifetime Constraints<br/>latest Batch = candidate Batch<br/>Todo = resolved]
    ROLLBACK[整体 rollback candidate<br/>不接受任何候选 Constraint]
    FEEDBACK[把实际 Patch、candidate Batch、Effect 原因<br/>反馈到同一个 Solve 会话]
    CLEANUP[若已 stage candidate<br/>先执行 best-effort rollback]
    TECH_KIND{异常类型}

    START --> LOAD --> LOAD_RESULT
    LOAD_RESULT -->|数据库异常| DB_ERROR
    LOAD_RESULT -->|operation 范围异常| OP_ERROR
    LOAD_RESULT -->|成功| RECOVER --> ENABLED
    ENABLED -->|否| UNSUPPORTED
    ENABLED -->|是| INIT --> REF_POOL
    REF_POOL -->|否| OP_ERROR
    REF_POOL -->|是| FULL_BATCH --> REVISION_OK
    REVISION_OK -->|否| CLEANUP
    REVISION_OK -->|是| RATE
    RATE -->|是| PASSED
    RATE -->|否| PLAN_BUDGET
    PLAN_BUDGET -->|否| PLAN_EXHAUSTED
    PLAN_BUDGET -->|是| RECORD_BATCH --> PLAN --> PLAN_RESULT
    PLAN_RESULT -->|no_new_failure_work| NO_WORK
    PLAN_RESULT -->|plan_budget_exhausted| PLAN_EXHAUSTED
    PLAN_RESULT -->|planned| EXPAND --> TODO_LEFT
    TODO_LEFT -->|否| ROUND_DONE --> FULL_BATCH
    TODO_LEFT -->|是| SELECT_TODO --> SOLVE --> SOLVE_RESULT
    SOLVE_RESULT -->|明确结束| TODO_FINISH --> TODO_LEFT
    SOLVE_RESULT -->|patch_ready| PATCH_REQ --> PATCH --> PATCH_RESULT
    PATCH_RESULT -->|编译失败 / 预算耗尽| PATCH_FAILED --> SOLVE
    PATCH_RESULT -->|ValidatedParameterPatch| PREPARE --> HAS_GENERATOR
    HAS_GENERATOR -->|是| STAGE --> CANDIDATE_BATCH
    HAS_GENERATOR -->|否| NO_STAGE --> CANDIDATE_BATCH
    CANDIDATE_BATCH --> CANDIDATE_REV
    CANDIDATE_REV -->|否| CLEANUP
    CANDIDATE_REV -->|是| EFFECT --> EFFECT_RESULT
    EFFECT_RESULT -->|resolved_without_regression| ACCEPT --> TODO_LEFT
    EFFECT_RESULT -->|unresolved / regression / unknown| ROLLBACK --> FEEDBACK --> SOLVE
    FULL_BATCH -. 技术异常 .-> TECH_KIND
    STAGE -. 技术异常 .-> CLEANUP
    CANDIDATE_BATCH -. 技术异常 .-> CLEANUP
    EFFECT -. 技术异常 .-> CLEANUP

    CLEANUP --> TECH_KIND
    TECH_KIND -->|SQLAlchemyError| DB_ERROR
    TECH_KIND -->|其他 operation 异常| OP_ERROR

    classDef process fill:#082f49,stroke:#22d3ee,color:#e2e8f0;
    classDef llm fill:#2e1065,stroke:#a78bfa,color:#f5f3ff;
    classDef decision fill:#422006,stroke:#fbbf24,color:#fef3c7;
    classDef success fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef failure fill:#4c0519,stroke:#fb7185,color:#ffe4e6;
    classDef data fill:#172554,stroke:#60a5fa,color:#dbeafe;
    classDef neutral fill:#1e293b,stroke:#94a3b8,color:#e2e8f0;

    class START,LOAD,RECOVER,INIT,FULL_BATCH,RECORD_BATCH,EXPAND,ROUND_DONE,SELECT_TODO,PREPARE,STAGE,NO_STAGE,CANDIDATE_BATCH,FEEDBACK process;
    class PLAN,SOLVE,PATCH,EFFECT llm;
    class LOAD_RESULT,ENABLED,REF_POOL,REVISION_OK,RATE,PLAN_BUDGET,PLAN_RESULT,TODO_LEFT,SOLVE_RESULT,PATCH_RESULT,HAS_GENERATOR,CANDIDATE_REV,EFFECT_RESULT,TECH_KIND decision;
    class PASSED,ACCEPT success;
    class DB_ERROR,OP_ERROR,PLAN_EXHAUSTED,PATCH_FAILED,ROLLBACK,CLEANUP failure;
    class PATCH_REQ data;
    class UNSUPPORTED,NO_WORK,TODO_FINISH neutral;
```

### 总流程的关键语义

- 一次 Smoke run 始终复用同一个 seed；Supervisor 发起的新重试在未显式指定 seed 时生成新 seed。
- 每轮 Plan 产出的 Todo 是固定快照。当前轮处理过程中出现的新 failure 只进入下一轮 Plan。
- 本轮后续 Todo 使用最新已接受 Batch，因此可以由 Solve 返回 `already_absent`。
- Todo 结束不表示 Smoke 通过。只有最新完整 Batch 达到 2xx 阈值才返回 `passed`。
- Generator 和 Constraint 作为一个候选整体接受或整体回滚，不存在 Group 级或输入级部分接受。

## 2. Plan Agent 协议

```mermaid
flowchart TD
    P_START([每个外层轮次创建全新 Plan 会话])
    P_INPUT[/SmokePlanRequest<br/><br/>operation_key<br/>最新完整 Batch<br/>每个 case 的生成值、脱敏请求、响应与错误<br/>临时代号 C1...Cn<br/>failed_case_codes<br/>完整 App-lifetime 历史/]
    P_SCOPE[Plan 的语义职责<br/>识别独特 failure<br/>关联失败 cases<br/>确定处理顺序<br/>判断是否已无新工作<br/><br/>不分析 root cause / parameter / Patch]
    P_BUDGET{全局 Plan 输出预算<br/>仍有剩余?}
    P_EXHAUSTED([plan_budget_exhausted])
    P_LLM[[调用 THINK 模型<br/>本次响应计数 +1<br/>无工具]]
    P_VALID{结构与引用有效?}
    P_CORRECT[协议修正 Prompt<br/>保留在同一 Plan 会话]
    P_ACTION{action}
    P_NO_WORK([no_new_failure_work])
    P_PROCESS[/process<br/>todos:<br/>todo_id<br/>failure<br/>case_codes/]
    P_CODE_CHECK{代码校验通过?}
    P_INVALID_REASON[拒绝原因<br/>Todo ID 重复<br/>引用不存在的 case code<br/>未覆盖全部失败 case]
    P_EXPAND[按 Plan 顺序固定 Todo<br/>将 case_codes 展开成<br/>完整 FailureCaseEvidence]
    P_OUTPUT([输出 FailureTodo 快照<br/>下游不再使用 C/F/O 代号])

    P_START --> P_INPUT --> P_SCOPE --> P_BUDGET
    P_BUDGET -->|否| P_EXHAUSTED
    P_BUDGET -->|是| P_LLM --> P_VALID
    P_VALID -->|否| P_CORRECT --> P_BUDGET
    P_VALID -->|是| P_ACTION
    P_ACTION -->|no_new_failure_work| P_NO_WORK
    P_ACTION -->|process| P_PROCESS --> P_CODE_CHECK
    P_CODE_CHECK -->|否| P_INVALID_REASON --> P_CORRECT
    P_CODE_CHECK -->|是| P_EXPAND --> P_OUTPUT

    classDef llm fill:#2e1065,stroke:#a78bfa,color:#f5f3ff;
    classDef decision fill:#422006,stroke:#fbbf24,color:#fef3c7;
    classDef input fill:#172554,stroke:#60a5fa,color:#dbeafe;
    classDef success fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef failure fill:#4c0519,stroke:#fb7185,color:#ffe4e6;
    classDef process fill:#082f49,stroke:#22d3ee,color:#e2e8f0;

    class P_LLM llm;
    class P_BUDGET,P_VALID,P_ACTION,P_CODE_CHECK decision;
    class P_INPUT,P_PROCESS input;
    class P_OUTPUT success;
    class P_EXHAUSTED,P_INVALID_REASON failure;
    class P_START,P_SCOPE,P_CORRECT,P_EXPAND,P_NO_WORK process;
```

Plan 的代码只检查结构、引用存在性和失败 case 覆盖，不判断两个 failure 在语义上是否真的独特。语义去重由 Plan 根据完整历史负责。

## 3. Failure Solver 协议

```mermaid
flowchart TD
    S_START([每个 Todo 创建新的 Failure Solver<br/>Todo 内保持连续会话])
    S_INPUT[/FailureSolveRequest<br/><br/>展开后的 FailureTodo 与完整关联 cases<br/>最新完整 Batch<br/>完整 operation IR / schema / testing snapshot<br/>当前 Generator 配置与已接受 Constraints<br/>可用 reference aliases / pools<br/>历史 Solve、HTTP、Patch、Effect 证据<br/>允许访问的 method + path template/]
    S_BUDGET{Solve 输出预算<br/>仍有剩余?}
    S_EXHAUSTED([Todo 结束<br/>solve_budget_exhausted])
    S_CHECKPOINT{下一个输出编号是<br/>continuation checkpoint?}
    S_CHECK_PROMPT[Checkpoint Prompt<br/>禁用全部工具<br/>默认输出 #10 / #20 / #30 / #40]
    S_NORMAL_PROMPT[正常调查 Prompt<br/>提供仅限当前 operation 的 HTTP Tool]
    S_LLM[[调用 THINK 模型<br/>本次响应计数 +1]]
    S_TOOL{响应包含 tool calls?}
    S_MIXED{同时包含 tool call<br/>与结构化 decision?}
    S_PRECHECK{全部 tool calls<br/>原子预检通过?}
    S_TOOL_REJECT[协议修正<br/>一个 HTTP 请求也不执行]
    S_HTTP[原子执行全部 HTTP 请求<br/>认证由运行时注入<br/>HTTP 执行本身不计输出]
    S_OBSERVE[把请求与响应加入 observations<br/>追加到同一个 Solve 会话]
    S_DECISION_VALID{结构化 decision<br/>符合当前 Prompt 协议?}
    S_CORRECT[协议修正 Prompt<br/>无效输出仍已消耗预算]
    S_MODE{当前是 checkpoint?}
    S_CHECK_ACTION{checkpoint 输出}
    S_CONTINUE[continue<br/>必须包含不同于历史的新方向]
    S_NORMAL_ACTION{正常调查输出}
    S_PATCH[/patch_ready<br/>PatchRequirement:<br/>root_cause<br/>affected_inputs<br/>desired_behavior<br/>acceptance_criteria/]
    S_FINISH[/finish + 明确终止状态/]
    S_TERMINAL([Todo 结束<br/>already_absent<br/>non_parameter<br/>dependency_related<br/>insufficient_evidence<br/>no_new_attempt])
    S_PATCH_FEEDBACK[/来自 Patch Agent 的反馈<br/>编译错误 / 样本 / 预算耗尽 / 尝试历史/]
    S_EFFECT_FEEDBACK[/来自 Effect 的反馈<br/>实际 Patch / candidate Batch<br/>unresolved / regression / unknown 原因/]

    S_START --> S_INPUT --> S_BUDGET
    S_BUDGET -->|否| S_EXHAUSTED
    S_BUDGET -->|是| S_CHECKPOINT
    S_CHECKPOINT -->|是| S_CHECK_PROMPT --> S_LLM
    S_CHECKPOINT -->|否| S_NORMAL_PROMPT --> S_LLM
    S_LLM --> S_TOOL
    S_TOOL -->|是| S_MIXED
    S_MIXED -->|是| S_TOOL_REJECT --> S_BUDGET
    S_MIXED -->|否| S_PRECHECK
    S_PRECHECK -->|否| S_TOOL_REJECT
    S_PRECHECK -->|是| S_HTTP --> S_OBSERVE --> S_BUDGET
    S_TOOL -->|否| S_DECISION_VALID
    S_DECISION_VALID -->|否| S_CORRECT --> S_BUDGET
    S_DECISION_VALID -->|是| S_MODE
    S_MODE -->|是| S_CHECK_ACTION
    S_CHECK_ACTION -->|continue| S_CONTINUE --> S_BUDGET
    S_CHECK_ACTION -->|finish| S_FINISH --> S_TERMINAL
    S_MODE -->|否| S_NORMAL_ACTION
    S_NORMAL_ACTION -->|patch_ready| S_PATCH
    S_NORMAL_ACTION -->|finish| S_FINISH
    S_NORMAL_ACTION -->|continue 或其他非法动作| S_CORRECT
    S_PATCH_FEEDBACK --> S_BUDGET
    S_EFFECT_FEEDBACK --> S_BUDGET

    classDef llm fill:#2e1065,stroke:#a78bfa,color:#f5f3ff;
    classDef decision fill:#422006,stroke:#fbbf24,color:#fef3c7;
    classDef input fill:#172554,stroke:#60a5fa,color:#dbeafe;
    classDef success fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef failure fill:#4c0519,stroke:#fb7185,color:#ffe4e6;
    classDef process fill:#082f49,stroke:#22d3ee,color:#e2e8f0;
    classDef safety fill:#431407,stroke:#fb923c,color:#ffedd5;

    class S_LLM llm;
    class S_BUDGET,S_CHECKPOINT,S_TOOL,S_MIXED,S_PRECHECK,S_DECISION_VALID,S_MODE,S_CHECK_ACTION,S_NORMAL_ACTION decision;
    class S_INPUT,S_PATCH,S_FINISH,S_PATCH_FEEDBACK,S_EFFECT_FEEDBACK input;
    class S_TERMINAL success;
    class S_EXHAUSTED,S_TOOL_REJECT,S_CORRECT failure;
    class S_START,S_CHECK_PROMPT,S_NORMAL_PROMPT,S_OBSERVE,S_CONTINUE process;
    class S_HTTP safety;
```

### HTTP Tool 的代码边界

- 只有 Failure Solver 能调用 HTTP Tool。
- 请求只能使用当前 operation 的 HTTP method 和 path template。
- 同一模型输出内的全部 tool calls 会先统一预检；任何一个无效时，一个也不执行。
- tool-call 模型响应计入 Solve 输出预算，实际 HTTP 执行不计入。
- 正常输出不能混合 tool call 和结构化决定。

## 4. Parameter Patch Agent 协议

```mermaid
flowchart TD
    A_START([每个 PatchRequirement 创建新的 FAST Agent])
    A_INPUT[/ParameterPatchTask<br/><br/>Todo failure<br/>root_cause<br/>affected_inputs<br/>desired_behavior<br/>acceptance_criteria<br/>当前 Generator 配置<br/>已接受 Constraints<br/>reference aliases / pool values<br/>case_count<br/>历史 Patch 与 Effect 尝试/]
    A_BUDGET{Patch 输出预算<br/>仍有剩余?}
    A_EXHAUSTED[/ParameterPatchFailure<br/>output_budget_exhausted<br/>完整错误与尝试历史/]
    A_BACK_SOLVE([反馈同一个 Solve 会话<br/>Solve 可重新形成 PatchRequirement])
    A_LLM[[调用 FAST 模型<br/>本次响应计数 +1<br/>无 HTTP 工具]]
    A_VALID{输出协议有效?}
    A_CORRECT[协议修正<br/>无效输出仍消耗预算]
    A_ACTION{action}
    A_PROPOSE[/propose<br/>完整 replacement Patch<br/>Generator changes + Constraints/]
    A_ACCEPT[/accept<br/>接受最新已采样候选 Patch/]
    A_COMPILE{代码安全与可执行性校验}
    A_SCOPE{affected_inputs 范围与<br/>system-managed input 边界通过?}
    A_TYPE{Generator / Constraint<br/>schema 与类型编译通过?}
    A_SAT{Constraint 可满足?}
    A_REF{reference alias、类型与<br/>非空 pool 校验通过?}
    A_COMPILE_FAIL[返回完整编译错误<br/>要求同一 Agent 给替代 Patch]
    A_SAMPLE[使用候选 Patch 生成<br/>恰好 case_count 个本地样本<br/>case_count 范围 1–20]
    A_REVIEW_INPUT[/回送同一 FAST 会话<br/>完整候选 Patch<br/>编译结果<br/>全部动态样本<br/>实际 reference pool values/]
    A_ACCEPT_VALID{accept 指向<br/>最新已验证候选?}
    A_ACCEPT_INVALID[拒绝 accept<br/>尚未采样或版本不匹配]
    A_OUTPUT([ValidatedParameterPatch<br/>实际 Patch<br/>samples<br/>outputs_used<br/>attempt_history])

    A_START --> A_INPUT --> A_BUDGET
    A_BUDGET -->|否| A_EXHAUSTED --> A_BACK_SOLVE
    A_BUDGET -->|是| A_LLM --> A_VALID
    A_VALID -->|否| A_CORRECT --> A_BUDGET
    A_VALID -->|是| A_ACTION
    A_ACTION -->|propose| A_PROPOSE --> A_COMPILE
    A_COMPILE --> A_SCOPE
    A_SCOPE -->|否| A_COMPILE_FAIL
    A_SCOPE -->|是| A_TYPE
    A_TYPE -->|否| A_COMPILE_FAIL
    A_TYPE -->|是| A_SAT
    A_SAT -->|否| A_COMPILE_FAIL
    A_SAT -->|是| A_REF
    A_REF -->|否| A_COMPILE_FAIL --> A_BUDGET
    A_REF -->|是| A_SAMPLE --> A_REVIEW_INPUT --> A_BUDGET
    A_ACTION -->|accept| A_ACCEPT --> A_ACCEPT_VALID
    A_ACCEPT_VALID -->|否| A_ACCEPT_INVALID --> A_BUDGET
    A_ACCEPT_VALID -->|是| A_OUTPUT

    classDef llm fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef decision fill:#422006,stroke:#fbbf24,color:#fef3c7;
    classDef input fill:#172554,stroke:#60a5fa,color:#dbeafe;
    classDef success fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef failure fill:#4c0519,stroke:#fb7185,color:#ffe4e6;
    classDef process fill:#082f49,stroke:#22d3ee,color:#e2e8f0;
    classDef safety fill:#431407,stroke:#fb923c,color:#ffedd5;

    class A_LLM llm;
    class A_BUDGET,A_VALID,A_ACTION,A_COMPILE,A_SCOPE,A_TYPE,A_SAT,A_REF,A_ACCEPT_VALID decision;
    class A_INPUT,A_EXHAUSTED,A_PROPOSE,A_ACCEPT,A_REVIEW_INPUT input;
    class A_OUTPUT success;
    class A_CORRECT,A_COMPILE_FAIL,A_ACCEPT_INVALID failure;
    class A_START,A_SAMPLE,A_BACK_SOLVE process;
```

Patch Agent 只负责生成和自审候选 Patch，不直接修改 Catalog 或数据库。真正的 candidate stage、完整 Batch 和原子验收由 OperationSmokeAgent 协调。

## 5. Effect Validator 协议

```mermaid
flowchart TD
    E_START([每个候选 Patch 创建新的 Effect Validator])
    E_INPUT[/SmokeEffectRequest<br/><br/>展开后的 FailureTodo<br/>完整 PatchRequirement<br/>实际 ValidatedParameterPatch<br/>动态样本与 Patch 尝试历史<br/>before_batch：候选前最新已接受完整 Batch<br/>candidate_batch：完整同 seed Batch<br/>完整请求、生成值、响应与历史/]
    E_BUDGET{Effect 输出预算<br/>仍有剩余?}
    E_LLM[[调用 THINK 模型<br/>本次响应计数 +1<br/>无工具]]
    E_VALID{协议输出有效?}
    E_FIRST_INVALID{这是第一次无效输出?}
    E_CORRECT[协议修正 Prompt]
    E_UNKNOWN[/强制结果 unknown<br/>第二次仍无效或预算耗尽/]
    E_RESULT{Effect 结论}
    E_RESOLVED[/resolved_without_regression<br/>目标 failure 已解决<br/>此前成功 case 与已解决 failure 未回归/]
    E_UNRESOLVED[/unresolved<br/>目标 failure 未解决/]
    E_REGRESSION[/regression<br/>产生此前没有的回归/]
    E_EXPLICIT_UNKNOWN[/unknown<br/>证据不足以判断/]
    E_ACCEPT[原子接受候选<br/>finalize Generator candidate<br/>加入 runtime Constraints<br/>Todo = resolved]
    E_ROLLBACK[整体 rollback<br/>Generator 与 Constraints 均不接受]
    E_FEEDBACK[把完整 candidate Batch、Patch 与 Effect 原因<br/>反馈同一个 Solve 会话]

    E_START --> E_INPUT --> E_BUDGET
    E_BUDGET -->|是| E_LLM --> E_VALID
    E_BUDGET -->|否| E_UNKNOWN
    E_VALID -->|否| E_FIRST_INVALID
    E_FIRST_INVALID -->|是| E_CORRECT --> E_BUDGET
    E_FIRST_INVALID -->|否| E_UNKNOWN
    E_VALID -->|是| E_RESULT
    E_RESULT -->|resolved_without_regression| E_RESOLVED --> E_ACCEPT
    E_RESULT -->|unresolved| E_UNRESOLVED --> E_ROLLBACK
    E_RESULT -->|regression| E_REGRESSION --> E_ROLLBACK
    E_RESULT -->|unknown| E_EXPLICIT_UNKNOWN --> E_ROLLBACK
    E_UNKNOWN --> E_ROLLBACK
    E_ROLLBACK --> E_FEEDBACK

    classDef llm fill:#2e1065,stroke:#a78bfa,color:#f5f3ff;
    classDef decision fill:#422006,stroke:#fbbf24,color:#fef3c7;
    classDef input fill:#172554,stroke:#60a5fa,color:#dbeafe;
    classDef success fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef failure fill:#4c0519,stroke:#fb7185,color:#ffe4e6;
    classDef process fill:#082f49,stroke:#22d3ee,color:#e2e8f0;

    class E_LLM llm;
    class E_BUDGET,E_VALID,E_FIRST_INVALID,E_RESULT decision;
    class E_INPUT,E_RESOLVED,E_UNRESOLVED,E_REGRESSION,E_EXPLICIT_UNKNOWN,E_UNKNOWN input;
    class E_ACCEPT success;
    class E_ROLLBACK failure;
    class E_START,E_CORRECT,E_FEEDBACK process;
```

`resolved_without_regression` 是唯一接受候选 Patch 的结果。代码不再使用“全局成功率提高”作为强制接受条件，也不允许部分接受。

## 6. LLM 输入输出与预算总表

| 角色 | 会话生命周期 | 必需输入 | 允许输出 | 工具 | 默认输出预算 |
|---|---|---|---|---|---:|
| `smoke_plan` / THINK | 每个外层轮次全新会话 | 完整 Batch、临时代号、所有失败 case、完整历史 | `process(todos)` 或 `no_new_failure_work` | 无 | 50，跨外层轮次累计 |
| `failure_solver` / THINK | 每个 Todo 新 Agent；Todo 内连续 | 展开 Todo、最新 Batch、operation IR、Generator、Constraints、references、完整历史 | HTTP tool calls；`patch_ready`；`finish`；checkpoint 的 `continue` | 当前 operation HTTP Tool | 50 / Todo |
| `parameter_patch_agent` / FAST | 每个 PatchRequirement 新 Agent | 根因、PatchRequirement、当前配置、Constraints、reference pools、case_count、历史 | `propose` 或看到编译样本后的 `accept` | 无 | 20 / PatchRequirement |
| `smoke_effect` / THINK | 每个候选 Patch 新 Agent | Todo、Requirement、实际 Patch、before/candidate Batch、历史 | `resolved_without_regression`、`unresolved`、`regression`、`unknown` | 无 | 2 / candidate |

所有 LLM 响应都计入对应预算，包括：

- 有效结构化输出；
- 无效 JSON 或无效字段；
- 包含 tool calls 的输出；
- 协议修正后的再次输出。

HTTP 请求的实际执行不计为 LLM 输出。

## 7. 请求字段

| 字段 | 默认值 | 含义 |
|---|---:|---|
| `operation_key` | 必填 | 本次测试的 operation |
| `case_count` | `10` | 每个完整 Batch 和 Patch 动态样本数量，范围 1–20 |
| `success_rate_threshold` | `0.8` | 最新完整 Batch 达到该 2xx 比例才允许 `passed` |
| `seed` | 可选 | 同一次 Smoke run 始终复用；未提供时自动生成 |
| `max_plan_outputs` | `50` | Plan 跨外层轮次的总输出预算 |
| `max_solve_outputs_per_todo` | `50` | 每个 Todo 的 Solve 输出预算 |
| `max_patch_outputs` | `20` | 每个 PatchRequirement 的 Patch Agent 输出预算 |
| `max_effect_outputs` | `2` | 每次 Effect 验证的输出预算 |
| `continuation_interval` | `10` | Solve 每隔多少次输出进入无工具继续检查 |

## 8. Solve Todo 终止状态

| 状态 | 含义 |
|---|---|
| `resolved` | 候选 Patch 已通过 Effect 并被原子接受 |
| `already_absent` | 使用最新 Batch 时该 failure 已不存在 |
| `non_parameter` | 不是参数生成或 Constraint 可以修复的问题 |
| `dependency_related` | 属于 operation dependencies，当前 Todo 不继续 |
| `insufficient_evidence` | 证据不足，无法形成可靠的新调查或 Patch |
| `no_new_attempt` | 账本中没有尚未尝试的新方向 |
| `solve_budget_exhausted` | Solve 达到硬输出预算 |

## 9. App 生命周期状态与持久化

```mermaid
flowchart LR
    BATCH[完整 Batch 证据]
    PLAN_H[Plan 输出]
    SOLVE_H[Solve 输出与 HTTP observations]
    PATCH_H[PatchRequirement<br/>实际 Patch<br/>动态样本与尝试]
    EFFECT_H[candidate Batch 与 Effect]
    LEDGER[(Operation 隔离<br/>App-memory Ledger)]
    CANDIDATE[(Catalog candidate revision)]
    GENERATOR[(已接受 Generator 配置)]
    CONSTRAINTS[(已接受 runtime Constraints<br/>仅 App 内存)]
    ROLLED_BACK([Candidate rejected / rollback<br/>不产生持久化修改])
    NEXT[后续 Todo / 外层轮次<br/>同一 App 的 Supervisor 重试]
    CLOSE([RESTScopeApp.close])
    CLEARED([释放原始证据与 Constraints])

    BATCH --> LEDGER
    PLAN_H --> LEDGER
    SOLVE_H --> LEDGER
    PATCH_H --> LEDGER
    EFFECT_H --> LEDGER
    LEDGER --> NEXT
    CANDIDATE -->|Effect 接受| GENERATOR
    CANDIDATE -->|Effect 未接受或异常| ROLLED_BACK
    CONSTRAINTS --> NEXT
    LEDGER --> CLOSE --> CLEARED
    CONSTRAINTS --> CLOSE

    classDef memory fill:#2e1065,stroke:#a78bfa,color:#f5f3ff;
    classDef persistent fill:#052e16,stroke:#34d399,color:#d1fae5;
    classDef evidence fill:#172554,stroke:#60a5fa,color:#dbeafe;
    classDef process fill:#082f49,stroke:#22d3ee,color:#e2e8f0;

    class LEDGER,CONSTRAINTS memory;
    class CANDIDATE,GENERATOR persistent;
    class BATCH,PLAN_H,SOLVE_H,PATCH_H,EFFECT_H evidence;
    class NEXT,CLOSE,CLEARED,ROLLED_BACK process;
```

- Generator 修改通过 Catalog candidate revision 持久化。
- 已接受 Constraint 只保存在 App 内存中，可跨 Todo、轮次和同一 App 的 Supervisor 重试。
- App 关闭后，原始响应、LLM 调查历史和 runtime Constraints 全部释放。
- 不新增数据库表，不持久化 LLM reasoning、计划、队列或一般 Agent memory。

## 10. Prompt 上下文装载

Prompt 输入预算：

```text
context_window_tokens - max_tokens - 2048
```

默认：

- `context_window_tokens = 131072`
- `max_tokens = 8192`
- 启动时必须满足 `max_tokens < context_window_tokens`

装载优先级：

1. 当前 Todo；
2. 最新完整 Batch；
3. operation/schema/testing 配置；
4. 当前连续 Solve/Patch 会话；
5. 最近一次 Effect；
6. 旧历史按从新到旧装载。

只有超过上下文窗口时才摘要旧历史。单个当前响应仍然超限时，保留数据结构、原始大小、头尾内容，并显式标记 `context_truncated`。
