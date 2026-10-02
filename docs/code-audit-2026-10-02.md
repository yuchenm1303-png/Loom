# Loom 代码审查（2026-10-02）

后续修复已实施，见 [修复记录与验证](audit-fixes-2026-10-02.md)。下文保留审查时的原始证据和测试状态。

本次为审查任务，未修改产品行为。范围包含全仓代码清单、Python runtime/存储/补丁/权限边界、Electron、React/Web bridge、浏览器扩展、账号和模型/Web 网关，以及测试和 CI 配置。仓库有 623 个受 Git 管理的 Python/JS/TS 类源文件（含测试和脚本）、258 个 tests 下的文件；进行了重点路径人工审查和全套测试验证，不声称每一个文件的每一行都已人工审阅。未进行真实账号、生产部署、真实浏览器和桌面操作的端到端验收，也未进行依赖漏洞数据库扫描。

优先级：P1 为高影响可靠性或权限问题，P2 为普通缺陷或明确性能问题。下面将直接复现、代码证据和建议分开。

## 需要优先处理的问题

### 1. [P1，代码证据] 远程设备选择与实际执行目标不一致

- `services/loom_web_gateway/app.py:274`：设备表只以 user_id 为键；`:415` 用新连接覆盖旧 Host，并关闭旧连接；`:310` 将请求送到该账号当前的 Host。
- `desktop-react/src/webBridge.ts:123` 附近生成 WebSocket URL 时删除 device 参数；`:371` 的 selectWebDevice 是空操作；activateWebRemoteDevice 只写 sessionStorage 并刷新页面。
- `desktop-react/src/components/RemoteDevicesPanel.tsx` 对用户承诺必须明确选择远程机器、不会自动回退。

触发：浏览器绑定 A，账号在 B 登录并连接中继；此后浏览器发出的文件或命令请求可以落到 B，而本地保存的选择仍为 A。界面的授权语义与实际路由不一致，具有操作错机器的风险。需要把目标设备 ID 纳入服务端绑定，并在目标离线时拒绝执行；如果产品只支持一个 Host，则必须同步收敛界面的设备选择功能和文案。

### 2. [P1，代码证据] 已建立的中继连接没有权限撤销复核

- `services/loom_web_gateway/app.py:337`、`:381` 的两个 WebSocket 端点只在握手前认证。
- 后续 ping/invoke/notification 循环不重新检查 access token、session 撤销或用户禁用。
- 账号服务已有管理员撤销会话/禁用账号能力，但 Web 中继没有相应的断连或周期复核机制。

结果：从代码看，撤销会话或禁用账号不能及时阻止现有连接继续发送控制请求，直到连接断开。建议使用有界周期的会话复核或撤销通知；续期时应绑定 session 身份，避免正常 access token 轮换误伤合法连接。此项未使用真实服务做撤销端到端实验。

### 3. [P2，直接复现] 并发请求创建多个 WebSocket

`desktop-react/src/webBridge.ts:211–220` 在判断 socketPromise 为空之后，先 await accountRequest，再赋值 socketPromise；并发调用可以同时穿过该检查。

复现：提取当前 ensureSocket 函数，用仓库 TypeScript 编译器转译，模拟账户状态和 WebSocket，连续调用两次；实际创建了 **2 个 socket**。旧 socket 的 message 监听器仍然存在，可能重复处理通知；心跳句柄也可能被后开的连接覆盖。建议把认证和建连的整个过程纳入同一个立即设置的共享 Promise，并校验监听器所属连接。

### 4. [P2，代码证据] 设备在线状态字段不匹配

`services/loom_web_gateway/app.py:browser_status` 发送 online/device；`RemoteDevicesPanel.tsx:144` 仅根据 status.devices 构建在线 ID 集合，而服务端没有发送 devices。

因此设备详情可以出现，但当前电脑/远程卡片仍被显示为离线，selectedOnline 也为 false。需要统一状态协议，并覆盖真实服务端消息到 UI 状态的测试。

### 5. [P2，直接复现] 补丁编辑改变 CRLF，回滚也不能保证原始字节

`app/agent_runtime/patch_runtime.py:87`、`:268` 使用 read_text 默认换行转换；`:210`、`:279` 写出文本时使用 newline=""。因此读取时转换为 LF 的内容会直接写回。

复现：原文件为 `b'first\r\nsecond\r\n'`，只替换 second，结果为 `b'first\nchanged\n'`。未修改行也改变行尾，产生额外 diff；异常回滚的 before 也是已归一化文本，不能恢复原字节。建议明确保留行尾策略，回滚保存原字节，并验证 CRLF、混合行尾和失败回滚。

### 6. [P2，直接复现] 补丁读写大小限制单位不一致

`app/agent_runtime/patch_runtime.py:13–14` 将可读取大小限制为 1,000,000 字节，可写大小限制为 1,000,000 字符。

复现：新增 400,000 个汉字的文件成功，文件为 **1,200,000 字节**；随后再次编辑同一文件，被自己的读取限制拒绝。多字节 UTF-8 内容尤其容易触发。建议统一用 UTF-8 字节数做边界检查，或明确采用一致的读写限制。

### 7. [P2，代码证据] 每次日志追加都完整读取已有日志

`app/agent_runtime/storage.py:355` 每次追加调用 repair_tail；`app/agent_runtime/journal.py:102` 的 repair_tail 先 handle.read() 整个文件，才判断是否已以换行结尾。

日志正常时也做全量读取。对于大小相近的 N 条事件，累计读取量近似 O(N²)，持锁期间的内存和 I/O 随历史增长。`storage.py:361` 的 events() 还会全量读取/解析；runtime/model request 和 context_budget 的多个路径反复调用它。

建议：正常追加先检查最后一个字节，仅异常尾部向后扫描；为事件读取增加 offset/sequence/tail API，并缓存或索引近期 usage。此项基于实现的复杂度分析，未做生产数据性能基准。

### 8. [P2，直接复现函数路径] 登录接口未验证 JSON 顶层类型

`services/loom_web_gateway/app.py:137` 捕获 JSON 解析错误，却不验证解析后是 dict，随后调用 body.get。

复现：对当前函数 AST 单独执行，Request.json 返回 []，得到 `AttributeError: 'list' object has no attribute 'get'`；有效 JSON 的 null、数字也存在同类问题。路由没有将这种输入转换为客户端错误，会成为 500。建议使用请求 schema 或显式对象检查。由于本机未安装该服务独立的 FastAPI 依赖，未运行 HTTP 层实验。

### 9. [P2，代码证据] Web 登录续期没有并发协调，服务故障也可能触发轮换

`services/loom_web_gateway/app.py:99` 忽略 _authenticated_user 的状态码，只要没有 user 就尝试 refresh；两个并发浏览器请求还可能携带同一个旧 refresh cookie 同时请求轮换。账号服务 `server.py:455` 的 refresh 是一次性轮换，旧 token 不能重用。

潜在结果：并发请求中一个成功、另一个返回未认证；账户服务临时不可达也被混入认证失效路径。如果轮换成功后第二次 /auth/me 失败，函数会丢弃已轮换的 session，浏览器还保留旧 refresh cookie，后续无法恢复。建议区分 401/403 与故障，协调同一 session 的刷新，并保证轮换后的 cookie 得以传播。Electron accountClient 已有 refreshInFlight 和 outage 保留逻辑，可借鉴其契约。

### 10. [P2，测试证据] Electron 账号测试与当前默认地址策略不一致

`npm run test:electron` 出现 3 项失败：未配置地址时的 status、拒绝不安全地址时的 status、未配置地址时登录错误码。

`desktop-react/electron/accountClient.ts:89` 附近配置解析失败后会回退到 packaged 生产地址或开发 loopback 地址，因此 configured 为 true；测试还期待 false 和 ACCOUNT_SERVICE_UNCONFIGURED。这里不能直接判定为明文 HTTP 泄露：不安全地址确实没有被使用。需要明确“无配置使用默认地址”和“显式错误配置是否拒绝”的产品契约，再同步实现与测试。静默使用另一个服务地址也容易误导排障。

## 优化与维护性

- **优先处理日志热路径和历史分页**：thread_read 默认组装全部 turns/events；presentationOnly 减少返回字段，但仍全量加载历史。Transcript 渲染所有 turnBlocks，memo 能减少重绘，却不能限制 DOM 数量。建议事件游标、分页加载旧 turn、历史虚拟化，并用长会话量化验证。
- **降低运行时补丁耦合**：app/__init__.py 安装大量 meta_path patch；import_patch_chain.py 还会吞掉 peer finder 的任意异常。应逐步将行为合并到明确类方法/扩展点，并测试冷启动、导入顺序和重启。保留必要错误的可观测性。
- **拆分大模块**：SettingsPage.tsx 约 100 KB，browser_tools.py 约 91 KB，app_server.py 约 70 KB，runtime.py 约 67 KB。按权限、协议、状态和工具域逐步拆分，避免只按文件长度机械拆分。
- **中继资源约束**：每个 invoke 无限制 create_task，发送锁前没有有界队列；广播等待全部客户端。建议每连接并发上限、发送超时、有界缓冲和慢客户端断连策略。RelayHub 完全在进程内，扩为多个 worker/副本前需要共享路由或明确单实例部署限制。
- **构建可重复性**：CI 和 Web Dockerfile 使用 npm install；Docker 初始依赖层未复制 package-lock。建议锁文件构建并使用 npm ci；CI 加入 Electron、完整脚本测试和 Web gateway 集成测试。目前 React CI 只运行 streaming 脚本测试。
- **文档契约同步**：README 声称不持久化 private chain-of-thought，但 storage.py 序列化 message.reasoning。需要界定“供应商返回的 reasoning 文本”与 private reasoning 的区别，并准确描述保存行为、隐私和保留策略。reasoning_store.py 本身保存的是推理配置，不是推理内容，不能用其文件名判断。

## 功能缺口建议

以下为当前代码范围内未发现完整实现的功能建议，不等同于必须实现的 bug：

1. 账号自助修改/找回密码、邮箱验证、用户管理已登录设备及主动撤销会话。当前存在管理员撤销能力，不能用它代替用户自助流程。
2. 完整的多 Host 选择、执行目标校验、离线拒绝和设备状态协议。先修正当前界面和服务端行为不一致。
3. 会话、项目绑定和长期记忆的统一备份/恢复与数据迁移验收；现有日志/SQLite 持久化不等于用户可操作的完整备份方案。
4. 长任务断线后的可见恢复流程、统一诊断包和脱敏导出；现有恢复/Computer Use 日志能力应复用，而不是重复建设。
5. 内置模型的用户预算/配额和并发上限。当前网关检查 entitlement，但未见按用户限制模型调用开销的路径；是否需要取决于内置模型的产品策略。

## 处理顺序

先修设备路由和撤销权限，再统一 Web 状态/建连/续期协议；同时修复补丁文件保真和大小边界。随后优化事件日志，再扩展集成测试与账号自助能力。没有证据支持为此整体重写 Loom。

## 验证结果

- `npm run typecheck`：通过，包含 renderer 和 Electron。
- `npm run test:ufo-scripts`：23 项通过，0 失败。
- `npm run test:electron`：3 项失败，均为账号默认地址/未配置契约测试，详见第 10 项；Electron 编译通过。
- Python 全套：**1911 passed、4 skipped、2 failed、356 warnings，326.85 秒**。
- Python 第一项失败：`test_mcp_v2_real_stdio_subprocess_round_trip`。项目 .venv 引用的 Python312 路径已经失效，本次使用桌面附带的 Python 加入 .venv 的 site-packages/win32 路径运行。MCP 真实子进程使用 sys.executable，未获取同一依赖路径，stderr 明确为 `ModuleNotFoundError: No module named 'mcp'`。这是本次测试环境限制，未归类为产品 MCP 缺陷。
- Python 第二项失败：`test_web_server_is_local_ui_and_rejects_cross_origin_post`。首次读取跨域 403 响应时出现 Windows 10053 ConnectionAbortedError；相同解释器单独重跑 **1 passed**。跨域请求处理在读取 body 前直接拒绝，未消费请求体的断连行为值得检查，但本次证据不足以断定根因或认为跨域保护失效。
- 356 条 warnings 主要包含 pywinauto COM 初始化警告和一个 asyncio 已关闭 pipe 的析构警告；需单独治理，不计入失败数。
- 直接复现：CRLF 被转换、UTF-8 大小边界矛盾、并发 ensureSocket 建立两个连接、登录函数接受 JSON 数组后异常。
- 完整 Web gateway HTTP 集成未运行：FastAPI 是该服务独立 requirements 的依赖，本机现有项目环境没有安装。登录输入问题使用当前函数 AST 隔离验证，其他网关问题为代码路径证据。
- 审查产物仅此文档；没有修改业务代码、凭据或用户已有验收目录。提交前检查文档 diff 和 Git 状态。
