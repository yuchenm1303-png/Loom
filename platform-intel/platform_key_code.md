# 风光储模拟教学仿真平台 — 关键代码与结构（给其他 agent 使用）

> 抓取时间：2026-09-29 | 当前用户：student13 | 当前工程：多人_3人一组_2

---

## 1. 外层壳 HTML（49KB，已存到 `.loom/attachments/598bfeed/platform_outer.html`）

URL：`http://cq.university.netts-dev.tsintergy.com/`

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="shortcut icon" href="favicons/guo_wang.ico">
  <title>风光储模拟教学仿真平台</title>
  <link rel="stylesheet" href="./umi.c67a7fed.css">

  <script>window.SYSTEM_NAME = '风光储模拟教学仿真平台'</script>
  <script>window.AUTH_CONTROL = 'true'</script>
  <script>window.MAP_JSON = {
    mapName: 'chongqing',
    zhName: '重庆市',
    mapJson: { /* 重庆 38 个区县的 GeoJSON（FeatureCollection） */ }
  }</script>
  <script>window.TOKEN_KEY = 'tmos_cq_university_token'</script>

  <script src="./framework.4c0f7514.js"></script>   <!-- React 18.1.0 生产版 + scheduler -->
</head>
<body>
  <div id="root"></div>
  <script src="./umi.522a5436.js"></script>         <!-- umi 4.x 入口 webpack bundle，4.5MB -->
</body>
</html>
```

**关键全局变量**：
- `window.SYSTEM_NAME` — 平台标题
- `window.AUTH_CONTROL='true'` — 启用权限控制
- `window.MAP_JSON` — 重庆地图（地图选区/可视化的底图）
- `window.TOKEN_KEY` — localStorage 里 JWT 的 key 名

**外层静态资源**（不需要登录即可拉）：
```
GET http://cq.university.netts-dev.tsintergy.com/                           # 49KB index.html
GET http://cq.university.netts-dev.tsintergy.com/framework.4c0f7514.js     # ~396KB React runtime
GET http://cq.university.netts-dev.tsintergy.com/umi.522a5436.js           # ~4.5MB  webpack entry
GET http://cq.university.netts-dev.tsintergy.com/umi.c67a7fed.css          # ~660KB Ant Design + 自研 tmos-* 组件库
GET http://cq.university.netts-dev.tsintergy.com/favicons/guo_wang.ico
```

---

## 2. webpack chunk 路由映射（umi.js 已存到 `.loom/scratch/umi.522a5436.js`，4.5MB）

`__webpack_require__.u()` 是 chunk-id → 文件名的映射。整个应用有 **40+ 个路由级 async chunk**，按模块分组：

### 2.1 市场仿真（**核心业务**，你报价系统要重点读）
| chunk id | 文件名 | 用途 |
|---|---|---|
| **3471** | `p__market-simulation__CaseManagement__index.4fb7445e.async.js` | **案例管理**（选择/复制/删除仿真案例） |
| **4761** | `p__market-simulation__CourseManagement__index.27af54b6.async.js` | 课程管理 |
| **5244** | `p__market-simulation__TheoreticalCourses__index.dada37ed.async.js` | 理论课程 |
| **9850** | `p__market-simulation__GridModel__index.ca8d4d51.async.js` | **电网模型**（电网拓扑/电网元件/元件标签） |
| **5584** | `p__market-simulation__GridOperating__index.d5486d8e.async.js` | **运行场景** |

### 2.2 现货市场三步（市场申报 / 出清 / 结果查看）
- 这三块**没有单独的 async chunk**，被合并在 `GridModel` / `GridOperating` 内的子路由里。
- 内层 iframe 加载后通过 `systemKey=integration-market` / `integration-grid` / `integration-page` 等 URL 参数切换子应用。

### 2.3 算法建模求解（GMS = Grid Modeling & Solving）
| chunk id | 文件名 |
|---|---|
| 1736 | `...__UnitGroup__index` |
| 5338 | `...__PowerPlant__index` |
| 5978 | `...__Section__index` |
| 8839 | `...__System__index` |
| 9146 | `...__UnitSet__index` |
| 9846 | `...__Unit__index` |
| 6994 | `...__ParameterConfiguration__index` |

### 2.4 基础应用平台（BAP，权限/菜单/日志/角色）
340 / 2219 / 3082 / 3303 / 3460 / 5236 / 6036 / 6786 / 6797 / 8555 / 8732 / 9817 / 1808

### 2.5 算法调度（Algorithm Scheduler）
- 8330 `AlgorithmSchedulingConfiguration__List`
- 1115 `AlgorithmSchedulingConfiguration__Detail`

### 2.6 运行监控预测（光伏/风电实时 + 预测）
- 234 PhotovoltaicRealTimeMonitor
- 6446 WindPowerRealTimeMonitor
- 6685 WindPowerForecast
- 8562 PhotovoltaicForecast

### 2.7 调度控制管理
- 1501 AlarmInfoManage
- 8754 DispatchInstructionsManage

### 2.8 异常页
- 563 Incompatible / 5981 403 / 6219 404 / 9856 500

### 2.9 用户/认证
- 2051 `Login__index`
- 5483 `ChangePassword__index`

### 2.10 共享/布局/库
- 1717 `layouts__index`（主布局壳）
- 3371 `Home__index`（首页）
- 2371 / 3620 / 7264 / 8404 — shared chunk
- 2849 `lodash-lib`（lodash 预编译）
- 3223 `react-flow-renderer-lib`（**电网拓扑图用了 react-flow**）
- 9107 `mockjs-lib`

> 完整 webpack `__webpack_require__.u` 表已含在 `.loom/scratch/umi.522a5436.js` 末尾，grep `__webpack_require__.u=` 可定位。

---

## 3. iframe 信息（**整个应用是单页 + iframe 套娃**）

| 项 | 值 |
|---|---|
| iframe 位置 | viewport (200, 64) → (1699, 888) |
| iframe 尺寸 | 1499 × 824 |
| 桥接 DOM | **看不到 iframe 内容**，只能用 viewport 坐标点 |
| 内层应用路径 | `http://cq.university.netts-dev.tsintergy.com/#/integration-page` |
| 内层应用切换 | URL hash + `systemKey` 参数（umi 的微前端/qiankun 模式） |

---

## 3.5 ★决定性发现：真正的业务应用不在这个域名下★

抠完 4 个核心 async chunk 后发现：**当前域名 `cq.university.netts-dev.tsintergy.com` 只是一个门户外壳**。
市场仿真、电网模型、案例管理这些页面，**每个都只是一个 iframe 包装器**，指向另一个独立前端应用（走单点换票）。

四个 chunk 的源码**完全同构**，只是 `redirectUrl` 不同（已核对原文）：

```js
// p__market-simulation__GridModel__index.ca8d4d51.async.js
var r = "".concat("", "/pmss/web/logincontroller/tmosCqUnitversityAuth");
// ...
n.default = function () {
  var e = i.Z.get(window.TOKEN_KEY);           // 读 localStorage 里的 token
  var n = "".concat(r).concat(util.f({
    outToken: e,
    redirectUrl: "#/Grid/Model"                // ← 真正应用内的 hash 路由
  }));
  return jsx("iframe", { src: n });            // 套一层 iframe 指向真应用
};
```

| chunk | redirectUrl（真应用路由） | 含义 |
|---|---|---|
| `GridModel` (9850) | `#/Grid/Model` | **电网模型**（市场申报页宿主） |
| `GridOperating` (5584) | `#/Grid/Operating` | **运行场景 / 出清** |
| `CaseManagement` (3471) | `#/CaseManagement` | **案例管理** |
| `TheoreticalCourses` (5244) | `#/TheoryLearning` | 理论课程 |

**换票入口（SSO）**：
```
GET /pmss/web/logincontroller/tmosCqUnitversityAuth?outToken=<localStorage[tmos_cq_university_token]>&redirectUrl=#/Grid/Model
```

**这意味着**：
- 报价 / 出清 / 结果的所有真实 API，**不在当前 umi 壳的 bundle 里**，而在 `/pmss/` 后面那个应用自己的 JS 里。
- 要接 API，下一步必须抓 **`/pmss/...` 那个应用的入口 HTML + 它的 webpack bundle**。
- token 通过 `outToken` query 传给真应用，真应用再拿它换自己的会话 —— 这就是鉴权链：`localStorage.tmos_cq_university_token` → `outToken` → 真应用 session。

> ⚠️ 抓真应用需要在 **EasyConnect 隧道内**（即关掉 WireGuard kill-switch）。WireGuard 一开，平台域名直接 `无法连接到远程服务器`。

---

## 4. 平台已知功能地图（来自截图 + DOM 观察）

### 4.1 顶部 5 步流程条（横向时序条）
1. **查看网架结构与元件参数**（电网拓扑图）
2. **查看市场边界数据**（负荷预测/新能源出力/联络线计划）
3. **进行市场主体申报**（机组报价）
4. **执行出清计算**（SCUC + SCED）
5. **查看出清结果**（节点电价/中标/潮流）

### 4.2 左侧导航树（内层应用，iframe 内）
- 电网模型
  - 电网拓扑
  - 电网元件
  - 元件标签
- 现货市场
  - **市场申报**（核心：你报价系统要改的就是这块）
  - **市场出清**（SCUC + SCED 触发）
  - **结果查看**（节点电价/中标/支路潮流）
- 运行场景

### 4.3 机组列表（10 机组 39 节点系统，"10机39节点系统_勿删1009"）
| 机组 | 电厂 | 燃料 |
|---|---|---|
| G30 | 电厂5 | 燃煤 |
| G31 | 电厂5 | 燃煤 |
| G32 | 电厂4 | 燃气 |
| G33 | 电厂4 | 燃气 |
| G34 | 电厂3 | 风电 |
| G35 | 电厂3 | 风电 |
| G36 | 电厂2 | 常规水电 |
| G37 | 电厂2 | 常规水电 |
| G38 | 电厂1 | 燃煤 |
| G39 | 电厂1 | 燃煤 |

### 4.4 市场申报表单字段（每机组）
```
最小技术出力费用  [ 元/小时 ]
热态启动报价     [ 元 ]
温态启动报价     [ 元 ]
冷态启动报价     [ 元 ]

[分段报价表]
段序号 | 起始出力(MW) | 终止出力(MW) | 价格(元/MWh) | 操作
1     | 0           | 210          | 1000         | 删除
2     | 210         | 420          | 1000         | 删除
3     | 420         | 630          | 1000         | 删除
4     | 630         | 840          | 1000         | 删除
5     | 840         | 1050         | 1000         | 增加 删除

按钮：导入数据 / 导出数据 / 同步到其他机组 / 保存
下方：价格-出力曲线图
```

### 4.5 出清算法（PPT + 平台日志披露）
- **阶段一 UC（机组组合）**：混合整数规划（MILP）
  - 目标函数：最小化总成本（启停 + 运行 + 备用）
  - 主要约束：系统功率平衡、备用需求、调频需求、直流潮流传输
- **阶段二 ED（经济调度）**：线性规划（LP）
  - 在 UC 基础上冻结 0-15 变量
  - 提取对偶变量 → 节点电价（LMP = energy + congestion + loss）
- **三段式出清**：单次集中出清 → 考虑安全约束 → 基于对偶解得边际价格

### 4.6 决策软件四步框架（PPT 披露，给报价软件用）
**预测 → 优化 → 报价 → 复盘**

---

## 5. 鉴权与 VPN

| 项 | 值 |
|---|---|
| VPN 门户 | `https://vpn.tsintergy.com:4434/portal/#!/` |
| VPN 账号 | `student01` ~ `student20` / 密码 `cdjx123` |
| 平台账号 | `student01` ~ `student20` / 密码 `admin123` |
| 当前用户 | `student13`（在平台内） |
| 当前组 | `多人_3人一组_2` |
| Token key | `tmos_cq_university_token`（存于 localStorage） |
| Token 来源 | 登录接口下发（未抓到具体路径，参考 `/api/auth/login` 等） |

> 平台域名 `cq.university.netts-dev.tsintergy.com` 公网 DNS 解析到 `192.168.1.111`（内网私有），**必须经 EasyConnect 隧道**才可访问。WireGuard kill-switch 开着时会拦截所有流量。

---

## 6. 已抓到的资源清单

| 文件 | 路径 | 用途 |
|---|---|---|
| `平台登录说明.docx.extracted.txt` | `.loom/attachments/9908c9fa/` | 登录说明文档（已读） |
| `platform_outer.html` | `.loom/attachments/598bfeed/` | **49KB 外层壳 HTML** |
| `image.png` / `image-2.png` | `.loom/attachments/598bfeed/` | 用户提供的截图（电网拓扑/市场申报 UI） |
| `umi.522a5436.js` | `.loom/scratch/` | **4.5MB 入口 webpack bundle** |
| `framework.4c0f7514.js` | `.loom/scratch/` | 396KB React runtime |
| `umi.c67a7fed.css` | `.loom/scratch/` | 全局样式（已下载） |
| 截图序列 | `.loom/scratch/shot01_initial.png` 等 | 当前 iframe 内布局快照 |

---

## 7. 报价系统要做的事（基于此平台的接口）

**目标**：自动化"市场申报 → 出清 → 复盘"的全流程，替代人工点 UI。

### 7.1 数据输入
- 读历史出清结果（结果查看 API）
- 读市场边界数据（负荷预测/新能源出力）
- 读对手机组历史报价（如果有披露）

### 7.2 决策输出
- 每机组 5~10 段分段报价曲线（起始/终止出力 + 价格）
- 启停报价（热/温/冷态）
- 最小技术出力费用

### 7.3 调用方式（两条路）
- **路 A（简单）**：通过浏览器桥接 + Playwright/Selenium 操作 UI（受 iframe 坐标漂移困扰）[[AI_LEDGER_INLINE_STICKER:pout_no]]
- **路 B（推荐）**：抓到后端 API（XHR/Fetch）后直接调，绕过 iframe
  - 需要先在 Loom 浏览器开 Network 面板 + 拦截出清请求
  - 找到申报保存/出清触发的具体 URL + payload + token 头

### 7.4 复盘
- 抓出清结果（中标量/节点价/收益）
- 与自己报价的预期中标对比，迭代预测模型