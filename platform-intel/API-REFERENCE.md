# 老师平台 API 参考（真应用 PMSS）— 接 TeacherPlatformAdapter 用

> 抓取时间 2026-09-29 | 用户 student13 | 当前 caseId=`48b2a370388348bdb2c14320f925136e`
> 域名 `cq.university.netts-dev.tsintergy.com`（必须经 EasyConnect 隧道）

---

## 0. 三层结构（关键认知）

```
① 外层门户 umi 壳   http://cq.../            ← 你最初看到的「风光储模拟教学仿真平台」
      └─ iframe
② 换票入口          /pmss/web/logincontroller/tmosCqUnitversityAuth?outToken=<JWT>&redirectUrl=#/XXX
      └─ 302
③ 真实业务应用 PMSS  /pmss/main.html#/Multi/Market/Cash/CashDeclare?projectId=...&netId=...
```

- **所有业务 API 都在 ③，前缀 `/pmss/web/`**
- ① 的 bundle（umi.js）里只有菜单路由和少量 `/web/...` 门户接口
- ② 用外层 `localStorage['tmos_cq_university_token']` 的 JWT 换 PMSS 会话

---

## 1. 鉴权链

| 环节 | 值 |
|---|---|
| 外层 token key | `tmos_cq_university_token`（`window.TOKEN_KEY`） |
| 默认 token key | `tmos_local_token` |
| JWT payload 样例 | `{"sub":"student13","username":"student13","nickName":"student13","iat":...}` |
| 换票接口 | `GET /pmss/web/logincontroller/tmosCqUnitversityAuth?outToken=<JWT>&redirectUrl=<urlencoded #/路径>` |
| 失败响应 | `{"retCode":"T100","retMsg":"系统走神了，请稍后再试","data":null}` |
| PMSS 读 token | 页面内 `getTokenCookie(name)` 从 `document.cookie` 取 |
| **实测 cookie 名** | `JSESSIONID`、`tmos_cq_university_token`、`jyzz-token`（三者都在 `document.cookie`） |
| PMSS 请求头 | 依赖 cookie 会话（`JSESSIONID`）+ `jyzz-token`；`credentials: 'include'` |
| 实测未带会话 | `GET /pmss/web/...` 返回 405（方法/会话不符）或 `T100` 系统错误 |

### 1.1 ★API 响应信封（实测）★
所有 `/pmss/web/*` 返回统一 JSON 信封：
```json
{ "retCode": "T000", "retMsg": "登录超时", "data": null }
```
| retCode | 含义 |
|---|---|
| `T000` | 业务码（成功/或带 retMsg 说明，如"登录超时"） |
| `T100` | 系统异常（"系统走神了，请稍后再试"） |
| HTTP 405 | 方法不允许（该 uri 是 POST，用了 GET） |
| HTTP 500 | 缺参数或会话无效 |

> 实测：从**外层页面**（非 iframe 内）直接 XHR 调 `project/list` → `{"retCode":"T000","retMsg":"登录超时"}`。
> 说明 **PMSS 会话校验不只看 cookie，还绑定了 iframe 上下文 / `jyzz-token` header**，
> 对接时需在真应用上下文内发起请求（或用等价的 header）。
| axios baseURL（PMSS） | `/pmss/web` |
| axios baseURL（门户） | `./web` |

---

## 2. ★已实测抓到的真实请求（iframe 内 performance API 导出）★

这是 HAR 级证据，全部来自用户真实登录态下的 iframe：

### 2.1 门户层
```
GET /web/loginIntegration/integrationRedirectUser?userId=e4e4fb0398089671019931ac8e8603be
GET /web/sysMenuManage/menu/userMenuTree
```

### 2.2 PMSS 项目/场景
```
GET /pmss/web/project/list?permissionType=0&projectType=1&pageSize=10&pageNo=1&version=base
GET /pmss/web/project/listSimulateCaseByProjectId?projectId=ae84f0de2e094ab3b26a737ce15d7854
GET /pmss/web/project/queryProcessGuide?projectId=ae84f0de2e094ab3b26a737ce15d7854
GET /pmss/web/tmScene/getMarketSystemAndScopIds?tmSceneId=432b473475e241d6974ecb0d149bd9c9
```

### 2.3 电网模型 / 图
```
GET /pmss/web/powerModel/getPowerModel?netId=7a02c3077449462fa013c0a5859f87a6
GET /pmss/web/powerModel/getComponentDictTree
GET /pmss/web/graph/attribute?netId=...
GET /pmss/web/graph/xml?netId=...&isEdit=false
GET /pmss/web/graph/setting?netId=...&type=color
```

### 2.4 机组 / 机组电量
```
GET /pmss/web/tmScene/spot/unit/getUnitDictTreeFilterByTypesWithMva
GET /pmss/web/tmScene/spot/unit/electricEnergy?scopeId=488a84ab65424d5d8a32efb565ca615c&unitId=720a5dcda34d4a1f8f3004892a491595
```

### 2.5 ★出清 / 结果★
```
GET /pmss/web/simulate/getClearingResult?caseId=<caseId>&marketTypeAtom=DA     # 日前出清结果
GET /pmss/web/simulate/getClearingResult?caseId=<caseId>&marketTypeAtom=RT     # 实时出清结果
GET /pmss/web/simulate/getSimulateDebugInfo?caseId=<caseId>
GET /pmss/web/simulate/getClearLogTargetFunction?caseId=<caseId>                # 出清日志/目标函数
GET /pmss/web/graph/model/lmp?isIndex=false&caseId=<caseId>                     # 节点电价(LMP)
GET /pmss/web/graph/model/branch?isIndex=false&caseId=<caseId>                  # 支路潮流
GET /pmss/web/marketResult/ClearingResultMap/queryClearingResultMap?caseId=<caseId>&marketType=DA&periodId=1
GET /pmss/web/project/version/queryClearingResultVersionList?sourceCaseId=<caseId>
GET /pmss/web/project/version/queryClearingResultVersionContext?sourceCaseId=<caseId>
GET /pmss/web/fineBi/getUrlByCode?code=<BI_CODE,...>
```

### 2.6 fineBi BI 看板 code 全表（= 结果查看页所有图表）
```
CASE-OVERVIEW-TOP-DA              CASE-OVERVIEW-TOP-RT
CASE-OVERVIEW-LEFT-BLOCK-UP-DN-DA CASE-OVERVIEW-LEFT-BLOCK-UP-DN-RT
CASE-OVERVIEW-MIDDLE-MARKET-PRICE-DA  CASE-OVERVIEW-MIDDLE-MARKET-PRICE-RT
CASE-OVERVIEW-BOTTON-DA           CASE-OVERVIEW-BOTTON-RT
CASE-OVERVIEW-UK-RT
CASE-NODELMP-TOP                  CASE-NODELMP-BOTTON      # 节点电价
CASE-SUBAREA-PRICE                                         # 分区电价
CASE-BRANCH-FLOW                  CASE-SECTION-FLOW        # 支路/断面潮流
CASE-UK-BRANCH-FLOW               CASE-UK-SECTION-FLOW
CASE-USER-LOAD-BID                CASE-USER-LOAD-UK-BID    # 用户报量
CASE-UNIT-BID                     CASE-UNIT-UK-BID         # 机组报价
```

---

## 3. 报价（申报）接口 —— 从 PMSS bundle 抠出

PMSS 用 `new XxxApi({uri, method, name})` 定义接口。申报相关：

| uri | method | 中文名 |
|---|---|---|
| `slLt/addDeclare` | **post** | 添加竞价申报 |
| `slLt/composite/declare` | get | 获取已申报序列 / 买五卖五数据 |
| `rcLt/transDeclareReplay/add` | post | 新建申报数据 |
| `rcLt/transDeclareReplay/export` | get | 导出 |
| `rcLt/transDeclareReplay/delete` | post | 删除申报数据 |
| `rcLt/transClearCheck/listDeclare` | get | 获取申报列表（交易申报复盘） |
| `rcLt/transClearCheck/...update` | post | 修改申报数据 |

> ⚠️ 注意：PMSS 里有两套申报（现货 spot=`slLt/...`、复盘 replay=`rcLt/...`）。
> 日前现货申报页面路由：`/pmss/main.html#/Multi/Market/Cash/CashDeclare`
> 出清页面路由：`#/Multi/Market/Cash/Clearing`
> 结果页面路由：`#/Multi/Market/Cash/Result/DayAhead`、`.../UnitResult`、`.../UserResult`

---

## 4. 门户 umi 壳里的资产分析接口（/web 前缀，需另探）

```
/assetAnalyse/getCaseGroups
/assetAnalyse/getPriceType
/assetAnalyse/listCaseOptions
/assetAnalyse/listMarketSystemOptions
/assetAnalyse/listMarketSystemType
/assetAnalyse/economic/getMarketOpen
/assetAnalyse/economic/getMarketPartIncome
/assetAnalyse/marketPrice/getMarketPriceAndLoadDemand
/assetAnalyse/marketPrice/getMarketPriceAndPgCost
/assetAnalyse/marketSecurity/getHighBidPriceRation
/assetAnalyse/marketSecurity/getLowBidPriceRation
/assetAnalyse/marketSecurity/getPriceRangeRatio
/assetAnalyse/marketSecurity/getTransSdBalanceIndex
/assetAnalyse/marketSecurity/getUnitBidPowerHoldRation
/marketAnalyse/analyse/getMarketEvaluationList
/daSpotReplay/createReplay | list | export | delete
/daTradeSummary/get
```

---

## 5. 枚举（从 bundle 抠，报价/出清逻辑用）

```js
// 任务类型
TASK_COMPOUND_BIDDING_CONTINUOUS_TRADE, TASK_DAY_DECLARE, TASK_RESULT

// 角色
machineDeclarer = "3" (机组),  userDeclarer = "4" (用户)

// 市场模式
{ normal: 0, marketMaker: 1 }

// 申报方式
{ notDeclare: 0, bothSides: 1, oneSide: 2 }

// 出清方式
{ uniformClearing: 0, separatelyClearing: 1 }

// 价格机制
{ nodePrice: 1, marginal: 2, ... }

// 出清状态
{ running:1, stopping:2, processing:3, tobePublished:4 }

// 交易范围
{ onTrade: 1, wholeNetwork: 0 }
```

---

## 6. 已下载产物（platform-intel/）

| 文件 | 大小 | 说明 |
|---|---|---|
| `pmss-main.html` | 2 KB | PMSS 入口 |
| `js_main.dd618d.js` | **5.5 MB** | ★PMSS 主 bundle（含全部业务接口定义） |
| `js_vendor.dd618d.js` | 1.0 MB | PMSS 第三方库 |
| `css_main.dd618d.css` | 499 KB | PMSS 样式 |
| `umi.522a5436.js` | 4.5 MB | 门户壳 bundle |
| `framework.4c0f7514.js` | 165 KB | 门户 React runtime |
| `platform_outer.html` | 50 KB | 门户 HTML |
| `api-catalog.txt` | — | 门户壳 58 条 url 定义 |
| `pmss-all-paths.txt` | — | PMSS 153 条路径 |
| `platform_key_code.md` | — | 初版结构文档 |

---

## 7. 建议的对接方式（给 TeacherPlatformAdapter）

1. **登录**：复用浏览器 cookie，或走 `/pmss/web/logincontroller/tmosCqUnitversityAuth` 换票。
2. **选场景**：`/pmss/web/project/list` → `listSimulateCaseByProjectId` → 拿 `caseId`/`netId`/`scopeId`。
3. **读机组**：`/pmss/web/tmScene/spot/unit/getUnitDictTreeFilterByTypesWithMva`（注意返回 405 → 需 POST 或带 cookie）。
4. **提交报价**：`slLt/addDeclare` (POST)。
5. **触发出清**：`/pmss/web/simulate/...`（需确认具体触发接口，见待办）。
6. **取结果**：`simulate/getClearingResult?marketTypeAtom=DA|RT` + `graph/model/lmp` + `graph/model/branch` + `ClearingResultMap/queryClearingResultMap`。

### 待确认（下一步）
- [x] 出清**触发**接口 → `POST simulate/execute`（已确认）
- [ ] `addDeclare` / `simulate/execute` 的完整 payload 结构
- [ ] 各接口返回 JSON 的真实字段（带 cookie 实测一次）

---

## 8. ★完整 API 目录（236 条，机器可读清单见 pmss-api-defs.txt）★

### 8.1 出清 / 仿真（核心！）
```
POST simulate/execute                  # ★执行出清/仿真（主入口，点「出清」就是它）
POST simulate/executeSync              # 同步执行
POST simulate/separateExecuteSync      # 分离执行
GET  simulate/parameter                # 仿真参数
GET  simulate/parameter/listConstraintRelax
GET  simulate/getSimulateDebugInfo?caseId
GET  simulate/getClearingResult?caseId&marketTypeAtom=DA|RT
GET  simulate/getClearLogTargetFunction?caseId
```

### 8.2 现货申报（日前/实时）
```
POST slLt/addDeclare                   # ★添加竞价申报
GET  slLt/composite/declare            # 获取竞标申报（买五卖五）
POST slLt/doRepealDeclare              # 撤销申报
GET  slLt/findDeclareListToCreator     # 申报列表（给创建者）
GET  slLt/getTimeSharingEnergy
GET  slLt/composite/contract / monitor
GET  slLt/bilateral/contract ; POST slLt/bilateral/doConfirm
```

### 8.3 中长期申报 / 复盘
```
POST rcLt/transDeclareReplay/add       # 新建申报数据
POST rcLt/transDeclareReplay/update    # 修改申报数据
POST rcLt/transDeclareReplay/delete    # 删除
POST rcLt/transDeclareReplay/executeCal  # 执行计算
GET  rcLt/transClearCheck/listDeclare          # 申报记录
GET  rcLt/transClearCheck/listCentralizeDeal   # 集中竞价成交序列
GET  rcLt/transClearCheck/listContinuityDeal
GET  rcLt/transSummary/listWholeDealResult
GET  rcLt/list / create / delete / getRcLtSummary
```

### 8.4 结果 / 结算
```
GET slLt/result/overview | detail | getDealOverview | listMarketPlayerDeal
GET slLt/result/listLtResultUnitAndUser | getMarketPlayerDetail
GET marketResult/unitSettle/getUnitDeclareNDeal        # 机组申报与成交
GET marketResult/ClearingResultMap/queryClearingResultMap?caseId&marketType&periodId
GET graph/model/lmp?caseId        # 节点电价
GET graph/model/branch?caseId     # 支路潮流
GET project/version/queryClearingResultVersionList|Context
```

### 8.5 场景 / 项目 / 流程
```
POST scene/create | copy | delete | rename | createAsync | deleteAsync
GET  scene/list | getSceneDateOptions
POST roomManage/roomCreate | roomEnterCheck ; room/role/agent
GET  project/list | listSimulateCaseByProjectId | simulateByProjectId | getProjectSimulateInfo
POST project/rename | remove
POST process | process/startSlProcessTaskInstance | suspend | complete
GET  process/findAllSlProcessTaskVO | judgeProcessStatusByProjectId
POST train/course* ; GET train/single/courses | projects
POST slCourse/publishCourse | cancelPublishCourse
```

### 8.6 机组参数 / 约束 / 断面
```
GET  unitParam/getUnitStaticParamList ; POST unitParam/doUpdateUnitStaticParam
GET  unitConstraint/list ; POST unitConstraint/save
GET  unitEnergyRange/list ; POST unitEnergyRange/save
POST sectionParam/section/* | secionDevice/* | transLimitTemporary/* | transLimitUsual/*
POST netGraph/update ; GET netGraph/judgeGraphValid ; POST powerModel/upload
GET  powerModel/unit/list | getMinePowerModelOptions | getPowerModel | getComponentDictTree
POST curveMgmt/* ; GET tmScene/curve/getCurve | getCurveDayByPeriod
GET  tmScene/spot/unit/electricEnergy | getUnitDictTreeFilterByTypesWithMva
```

### 8.7 智能代理 / 角色 / 市场定义
```
POST agent/addAgentModel | updateAgentModel | copy | delete | updateAuthTypeById
GET  roleSelect/getUnitInfos | getUserInfos | getAgentForAllUnSelectedUnit
POST roleSelect/intelligentAgent ; GET marketSystem/list | getBuiltInMarketSystemDict
GET  spotModel/listByType|detail ; POST spotModel | copy | rename | update
GET  version/algorithm | version/system
```

---

## 9. ★出清触发链路与 payload（从 saga 抠出）★

真实出清（点「执行出清/仿真」）的调用链：

```js
// 简化自 bundle 里的 redux-saga
await put(Ts.a.executeSync({ caseId, comment, marketTypeAtom }))   // GET simulate/executeSync
// 或
await put(Ts.a.calculateCase({ caseId, comment }))                 // POST simulate/execute
// 出清完成后，再拉日前+实时结果：
await all([
  put(fetchResult({ caseId, marketTypeAtom: 'DA' })),   // simulate/getClearingResult?marketTypeAtom=DA
  put(fetchResult({ caseId, marketTypeAtom: 'RT' })),   // simulate/getClearingResult?marketTypeAtom=RT
])
```

| 参数 | 说明 |
|---|---|
| `caseId` | 算例 ID（当前 `48b2a370388348bdb2c14320f925136e`） |
| `comment` | 出清备注 |
| `marketTypeAtom` | `DA`（日前）/ `RT`（实时） |

**出清状态判断（前端轮询用）**：
```js
data.isClearing   // 正在出清
data.simulateStatus
status === 0      // 完成
```

### 9.1 redux action ↔ API 映射（出清页）
```
calculateCase     → POST simulate/execute
calculateWhole    → simulate/execute (全模型)
separateExecute   → GET  simulate/separateExecuteSync   【甘肃】日前实时出清
executeSync       → GET  simulate/executeSync
releaseResult     → 发布结果
getParams         → GET  simulate/parameter
saveParams        → POST simulate/parameter
listConstraintRelax → GET simulate/parameter/listConstraintRelax
startClearingFlow → 启动出清流程
```

### 9.2 ★报价 payload 字段（`slLt/addDeclare` 调用点实抠）★

```js
// 直接来自 bundle 里 addDeclare(k) 的 k 构造
{
  projectId,            // 项目/场次
  tradePartyId,         // 交易主体 ID
  tradePartyName,       // 交易主体名
  tradePartyType,       // 主体类型
  tradeMethod,          // 交易方式
  dealRoleEleId,        // 成交角色元素 ID
  dealRoleName,         // 成交角色名
  dealRoleType,         // 成交角色类型
  declareNum,           // 申报电量/数量
  declarePrice,         // 申报价格
  deliveryNodeId,       // 交割节点 ID      ← 对应电网拓扑节点
  deliveryNodeType,     // 交割节点类型
  deliveryNodeName,     // 交割节点名
  tradeDirection,       // 交易方向（买/卖）
  yearCurveId,          // 年曲线 ID
  monthCurveId,         // 月曲线 ID
  periodNum,            // 时段号
  dayCurveId,           // 日曲线 ID
  scopeId,              // 场景/范围 ID
  statisticalTime,      // 统计时刻
  priceGrowthRatio,     // 报价增长比
}
```

> 中长期申报 `rcLt/transDeclareReplay/add` 同构。
> 现货日前（`#/Multi/Market/Cash/CashDeclare`）的分段报价曲线由表单直接构造，
> 若需要精确 body，在平台里点一次「保存报价」，用 §10 钩子抓取即可。

---

## 10. 运行时抓包钩子（已注入，待用户触发一次）

已在 iframe（同源）内 hook 了 `fetch` 与 `XMLHttpRequest`，捕获到 `iframe.contentWindow.__loomCalls`。

- 钩子状态：**已安装**，`count = 0`（尚未触发任何请求）
- 要拿到 `addDeclare` 的真实 body：**在平台里改一个机组报价 → 点保存 → 再点一次出清**
- 之后读取：
```js
JSON.stringify(document.querySelector('iframe').contentWindow.__loomCalls)
```
即可得到完整的 URL + method + payload。
