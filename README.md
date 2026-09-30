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

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已完成」并给出结论。
3. auditor 登录后只能看列表，没有提交表单。

## 超差盘点台（导航「盘点台」，`#/inventory`）

- 按创建时刻的小时窗整页呈现，窗为半开区间 `[start, end)`：创建时刻压线**起点入窗、压线终点不入窗**，前后端共用同一口径（服务端 `created_at__gte / __lt`）。
- 窗内**未结清**（待复核/复核中）的行不计入合格桶或超差桶，单列「未结清量」，不参与占比；占比分母为窗内已结清总数。
- 四类量（合格量 / 超差量 / 未结清量 / 窗内总量）、占比与「按窗重查明细」由接口 `GET /api/inventory/window?start=&end=`（或 `hours=`）**一次响应同源产出**，前端只展示不做加总。
- 操作员（machinist）可调窗宽（最近 Nh 小时或自定义起止），调窗当场服务端重算；复核员（auditor）只读，仅能刷新。
- 刷新按钮下方为口径总览一行（只陈述边界与分母规则，不含百分比）。
- 对账：故意超差三笔再压线合格一笔后，本窗超差占比升至 0.75、合格量为 1；窗收窄到不含这批时刻时结果为空、占比为空。

测试：`DJANGO_SETTINGS_MODULE=config.settings_test python3 manage.py test desk`

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```
