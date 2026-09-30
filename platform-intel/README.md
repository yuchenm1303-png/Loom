# 老师平台（PMSS）接口抓取成果 — 目录说明

> 抓取日期 2026-09-29 | 目标：给「电力报价决策软件 / PowerBid Lab」接老师平台 API

---

## 一句话结论

老师平台是**三层套娃**：

```
① 门户壳          http://cq.university.netts-dev.tsintergy.com/
      └─ iframe ② 换票  /pmss/web/logincontroller/tmosCqUnitversityAuth?outToken=<JWT>&redirectUrl=#/...
                  └─ 302 ③ 真应用 PMSS  /pmss/main.html#/Multi/Market/Cash/CashDeclare
```

**所有业务接口都在 ③，前缀 `/pmss/web/`。** 报价、出清、结果全在这里。

---

## 文件清单

| 文件 | 说明 |
|---|---|
| **API-REFERENCE.md** | ★主文档★ 三层结构 / 鉴权链 / 236 条 API / 报价与出清 payload / 响应信封 |
| pmss-api-defs.txt | 236 条 API 的 uri + method + 中文名（机器可读） |
| api-catalog.txt | 门户壳 58 条 url 定义 |
| pmss-all-paths.txt | PMSS bundle 里 153 条路径 |
| platform_key_code.md | 初版结构文档（门户壳 chunk 路由表、功能地图、出清算法） |
| pmss-main.html | PMSS 入口 HTML |
| **js_main.dd618d.js** | ★PMSS 主 bundle 5.5MB（所有接口定义都在里面） |
| js_vendor.dd618d.js | PMSS 第三方库 1.0MB |
| css_main.dd618d.css | PMSS 样式 499KB |
| umi.522a5436.js | 门户壳 bundle 4.5MB |
| framework.4c0f7514.js | 门户 React runtime 165KB |
| umi.c67a7fed.css | 门户样式 520KB |
| platform_outer.html | 门户 HTML 50KB |
| chunks/ | 门户 4 个业务 chunk（都是 iframe 换票包装器） |

---

## 最快的接入路径（6 步）

1. **鉴权**：拿 cookie `tmos_cq_university_token`（JWT）→ 换票 `/pmss/web/logincontroller/tmosCqUnitversityAuth?outToken=...&redirectUrl=...`；真应用另有 cookie `JSESSIONID`、`jyzz-token`。
2. **选场景**：`GET /pmss/web/project/list` → `GET /pmss/web/project/listSimulateCaseByProjectId?projectId=...` → 拿 `caseId` / `netId` / `scopeId`。
3. **读机组**：`GET /pmss/web/tmScene/spot/unit/getUnitDictTreeFilterByTypesWithMva`。
4. **提交报价**：`POST /pmss/web/slLt/addDeclare`（payload 字段见 API-REFERENCE.md §9.2）。
5. **触发出清**：`POST /pmss/web/simulate/execute`（或 `GET simulate/executeSync`）。
6. **取结果**：
   - `GET /pmss/web/simulate/getClearingResult?caseId=...&marketTypeAtom=DA|RT`
   - `GET /pmss/web/graph/model/lmp?caseId=...`（节点电价）
   - `GET /pmss/web/graph/model/branch?caseId=...`（支路潮流）
   - `GET /pmss/web/marketResult/ClearingResultMap/queryClearingResultMap?caseId=...&marketType=DA&periodId=1`

---

## 响应信封（所有 /pmss/web/* 统一）

```json
{ "retCode": "T000", "retMsg": "...", "data": { } }
```

| retCode | 含义 |
|---|---|
| T000 | 业务码（成功 / 带说明，如「登录超时」） |
| T100 | 系统异常（「系统走神了，请稍后再试」） |

- HTTP 405 = 方法不对（该接口是 POST）
- HTTP 500 = 缺参数 / 会话无效

---

## 环境依赖（重要）

- 域名 `cq.university.netts-dev.tsintergy.com` **公网解析到内网 192.168.1.111**，必须经 **EasyConnect 隧道**。
- **WireGuard 的 kill-switch 打开时会拦截 EasyConnect 隧道流量**，平台会报 `ERR_NETWORK_ACCESS_DENIED` / 无法连接。抓取时需关闭 WireGuard 或关掉 kill-switch。
- 登录后 App 截图请在真实登录态下进行（本轮抓取使用 student13）。

---

## 唯一未完成项

接口**返回 JSON 的字段类型**尚未逐条实测（平台当时已断开）。

补齐方法：连上平台后，在真应用里点一次「保存报价」和「出清」，读取注入的钩子：

```js
document.querySelector('iframe').contentWindow.__loomCalls
```

即可得到完整 URL + method + payload + 响应。
