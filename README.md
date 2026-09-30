# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Django 5 + django-ninja（ASGI / uvicorn） |
| 前端 | SolidJS + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |
| 鉴权 | JWT（python-jose），令牌存浏览器 localStorage |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3196 |
| 接口 | http://localhost:8196 |
| PostgreSQL | localhost:54396（库名 `cncoffset`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| machinist | machine123456 | 可提交刀补 |
| auditor | audit123456 | 只读列表 |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 超差盘点台（小时窗）

导航「盘点台」（`#/inventory`）按小时窗整页呈现，自上而下三段：

1. **上：选窗**——窗宽档位（1/2/4/8/12/24 小时）与窗止时刻（留空取当前整点）。操作员可调，复核员只读（控件禁用）；调窗后数字由服务端当场重算，前端不自行加总。
2. **中：四类量与占比**——合格、超差、未结清、合计（分母），数字与百分比字符串原样渲染服务端 `buckets`；合计为基准行，其百分比不计入任何桶。
3. **下：刷新 / 口径总览 / 按窗重查明细**——明细表每行带服务端标注的桶归属，并附聚合对明细的逐桶对账结果（误差恒为 0）。

口径（前后端一致）：

- 窗口按整点对齐、**左闭右开 `[窗起, 窗止)`**；创建时刻压线窗起计入，压线窗止不计入。
- 仅「已结清」（`status=done` 且结论明确）的行可入**合格桶 / 超差桶**。
- 创建时刻落在窗内但**尚未结清**（待复核 / 复核中 / 结论缺失）的行不计入合格桶或超差桶，单列**未结清**桶。
- 占比分母为四类合计（含未结清）。

接口：`GET /api/inventory?width_hours=1&end_at=2026-09-26T11:00`（`end_at` 可空）。
四类量、占比、明细、对账信息由同一个服务端聚合函数 `desk.inventory.compute_inventory` 一次产出（三路同源）：聚合即先取窗内明细再在服务端分桶，分桶结果同时汇总为 buckets 并逐桶对账，不存在前端加总或两次请求跨整点漂移。

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已完成」并给出结论。
3. auditor 登录后只能看列表，没有提交表单；进入盘点台时窗宽/窗止控件只读。
4. 盘点台：故意提交三笔超差、再让一笔合格创建时刻压线窗起，本窗超差占比应升高、合格量至少为 1；把窗收窄到不含这批时刻，占比应下降或本窗为空；对账误差始终为 0。

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```
