# TeaWither-01 · 茶萎凋台账

Django 5 + PostgreSQL 服务端渲染应用：Templates + HTMX + 自定义 CSS，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX（CDN）局部刷新列表
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4100** |
| Postgres | **5440**（容器内 5432） |

数据库账号：`teawither` / `teawither` / 库名 `teawither`

## 快速启动

```bash
cd TeaWither/TeaWither-01
docker compose up --build -d
```

浏览器打开：http://localhost:4100

演示账号：

- `admin` / `123456`（超级用户）
- `witherer` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 5440，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=5440
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4100
```

## 业务模型

1. **Garden（茶园）**：`name`、`altitudeBand`、`notes`
2. **Trough（萎凋槽）**：归属茶园、`troughCode`、`cultivar`、`loadKg`、状态 `loading|withering|ready`；同一茶园内槽位编号唯一
3. **WitherBatch（萎凋批次）**：归属槽位、`startedAt`、`targetMoisture`、`actualMoisture`（可空）、`rollGrade`
4. **TurnLedger（翻堆节拍账）**：归属槽位、`sequence`（翻堆序号）、`plannedAt`（计划翻堆时刻）、`doneAt`（实做时刻，可空）、`operator`（当班人）、`settled`（是否销账）；同一槽位内翻堆序号唯一

### 翻堆节拍账规则

账挂槽位，**未销账前该槽不得进入「可下槽」**。

- **建账**：仅当槽位正处于 `withering`（萎凋中）时才能建账；`loading`（装叶中）、`ready`（可下槽）两种槽态一律不准建账。翻堆序号在同槽内从 1 起自动递增且唯一。
- **销账**：销账动作写入实做时刻 `doneAt`，实做时刻不得早于计划翻堆时刻；并在**同一事务**内核对该槽最新萎凋批次已有实测含水率 `actualMoisture`，缺测（或无批次）则整笔回滚、拒绝销账。
- **放行「可下槽」**：槽状态改为 `ready` 时，未销账条数大于 0 即拒绝，中文提示「尚有 N 条翻堆节拍账待销账」；此外仍要求最新批次实测含水率已填且 ≤ 40%。
- **统一核对**：销账是否能完成、槽状态是否能放行，都调用同一个查询函数 `Trough.turn_settlement_check()`（返回未销账条数与最新批次），两条路径读到同一组事实。因此：
  - 节拍账表为空时，槽可正常改「可下槽」（含水率满足即可）；
  - 销账不查实测含水率，不算完成。

槽列表以徽标展示各槽「N 条待销账」，顶栏「翻堆节拍」可建账、销账。

**含水率规则（既有）**：将槽位状态设为 `ready`（可下槽）时，若最新批次的 `actualMoisture` 为空或大于 40，抛出中文 `ValidationError`。

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有茶园则只保证账号存在。亦可在环境变量 `TEAWITHER_AUTO_SEED=1` 时于 `post_migrate` 自动播种。

## 目录结构

```
TeaWither-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/           # 项目配置
  apps/gardens/     # 模型、视图、种子命令
  templates/        # Django 模板
  static/css/       # 自定义样式（茶绿色顶栏）
```
