# EHS 知识库（文件即数据库）

一个可直接部署在公司内网的 EHS 知识库：**磁盘上的 Markdown 文件就是全部数据**，
没有任何内容进数据库表。删掉一个 `.md`，网站上对应条目立即消失。

- 后端：Python FastAPI，启动时扫描目录建内存索引，watchdog 监听变化热重载（无需重启）
- 前端：单页，原生 JS + CSS，**不依赖任何 CDN / 外网**（内网可直接跑）
- 检索：jieba 中文分词 + 内存倒排索引，返回 `<mark>` 高亮片段
- 权限：同事只读免登录；管理员用环境变量口令进入管理视图，改动直接落盘
- 删除：只移入 `.trash` 并记 `operation.log`，保留 90 天，不做物理删除

---

## 一、目录结构

```
ehs-kb/                              # 程序目录（可放 /opt/ehs-kb 或 D:\ehs\app）
├── requirements.txt                 # 依赖清单
├── .env.example                     # 环境变量样例
├── README.md
├── app/                             # 后端
│   ├── __init__.py
│   ├── config.py                    # 全局配置（全部来自环境变量）
│   ├── safepath.py                  # 路径安全，防 ../ 越权
│   ├── indexer.py                   # 目录扫描 / frontmatter 解析 / Markdown 渲染 / 内存索引
│   ├── search.py                    # 中文分词、倒排索引、高亮片段
│   ├── watcher.py                   # watchdog 文件监听 + 防抖热重载
│   ├── oplog.py                     # 回收站、operation.log、90 天保留清理
│   └── main.py                      # FastAPI 路由（只读接口 + /api/admin/*）
├── static/                          # 前端（单页）
│   ├── index.html                   # 三栏布局：目录树 / 正文 / 抽屉
│   ├── style.css
│   └── app.js
├── deploy/                          # 部署
│   ├── ehs-kb.service               # Linux systemd 开机自启
│   ├── install_linux.sh             # Linux 一键安装
│   ├── install_windows.ps1          # Windows Server 一键安装（nssm 注册服务）
│   ├── backup.sh                    # Linux 每日备份
│   └── backup.bat                   # Windows 每日备份
└── docs/                            # ★ 知识库根目录（数据，生产环境指向 /srv/ehs/docs 或 D:\ehs\docs）
    ├── 危化品/
    │   ├── 硫酸储罐区安全管理规定.md          # 一篇知识 = 一个 .md
    │   └── 硫酸储罐区安全管理规定/            # ★ 同名文件夹 = 该篇的附件目录
    │       ├── 罐区平面图.png
    │       ├── 硫酸MSDS.pdf
    │       └── 罐区巡检记录模板.csv
    ├── 特种设备/  危废/  应急/  法规/  SOP/
    └── .trash/                       # 回收站（扫描时跳过）
        ├── operation.log             # 操作日志：时间 / 操作 / 文件 / 操作人 / 备注
        └── 20260902-023719-70aea6/…  # 每次删除一个批次目录，保留 90 天
```

**内容约定（只有这三条）**

1. 一篇知识 = 一个 `.md` 文件，放在分类目录下；
2. 需要附件就在旁边建一个**与 .md 同名的文件夹**，PDF / 图片 / Excel 丢进去，
   正文里用相对路径引用：`![平面图](硫酸储罐区安全管理规定/罐区平面图.png)`；
3. 元数据写在文件头部的 YAML frontmatter 里。

## 二、frontmatter 字段

```yaml
---
title: 硫酸储罐区安全管理规定     # 标题，缺省取文件名
category: 危化品                  # 分类，缺省取一级目录名
tags: [危化品, 储罐, 硫酸]        # 标签，也可写成 "危化品, 储罐" 逗号串
owner: 张道然                     # 责任人
effective_date: 2026-03-01        # 生效日期
review_date: 2026-09-20           # 复审日期（距今 < 30 天会进到期提醒面板）
status: 生效                      # 生效 / 废止（废止条目置灰、检索降权）
doc_no: EHS-CHM-012               # 其它自定义字段随便加，会原样保留
---
```

## 三、本地试跑（3 条命令）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
EHS_DOCS_ROOT=$PWD/docs EHS_ADMIN_TOKEN=test123 EHS_PORT=8080 .venv/bin/python -m app.main
# 浏览器打开 http://127.0.0.1:8080
```

> 注意：`jieba` 是源码包，若系统 setuptools 过旧会编译失败，**务必在 venv 里安装**
> （venv 自带新版 setuptools）。万一内网实在装不上，程序会自动退化为二元分词，检索仍可用。

---

## 四、Linux 部署（CentOS 7+ / Rocky / Ubuntu 20.04+）

```bash
# 1) 上传源码并执行一键安装（会建账号、装依赖、注册 systemd、开防火墙）
sudo EHS_ADMIN_TOKEN='你的口令' bash deploy/install_linux.sh

# 2) 常用运维命令
sudo systemctl status  ehs-kb          # 查看状态
sudo systemctl restart ehs-kb          # 重启（改口令后需要）
sudo journalctl -u ehs-kb -f           # 跟踪日志
tail -f /var/log/ehs-kb.log

# 3) 改口令 / 改端口
sudo vi /etc/ehs-kb.env && sudo systemctl restart ehs-kb

# 4) 注册每日备份（每天 01:30 打包到 /backup/ehs，保留 30 份）
sudo crontab -e
30 1 * * * /bin/bash /opt/ehs-kb/deploy/backup.sh >> /var/log/ehs-backup.log 2>&1

# 5) 访问： http://<内网IP>:8080
ip -4 addr | grep inet
```

手工安装（不想用脚本时）：

```bash
sudo useradd -r -s /sbin/nologin ehs
sudo mkdir -p /opt/ehs-kb /srv/ehs/docs && sudo cp -r . /opt/ehs-kb
cd /opt/ehs-kb && sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo cp deploy/ehs-kb.service /etc/systemd/system/
sudo cp .env.example /etc/ehs-kb.env && sudo chmod 600 /etc/ehs-kb.env   # 记得改口令
sudo chown -R ehs:ehs /opt/ehs-kb /srv/ehs
sudo systemctl daemon-reload && sudo systemctl enable --now ehs-kb
sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload
```

## 五、Windows Server 部署（2016 / 2019 / 2022）

前置：安装 [Python 3.10+](https://www.python.org/downloads/windows/)（勾选 *Add Python to PATH*），
下载 [nssm](https://nssm.cc/download)，把 `nssm.exe` 放进 `PATH` 或 `deploy\` 目录。

```powershell
# 管理员 PowerShell
Set-ExecutionPolicy Bypass -Scope Process -Force
cd C:\源码目录\ehs-kb
.\deploy\install_windows.ps1
# 脚本会：建 D:\ehs\{app,docs} → 建 venv 装依赖 → nssm 注册 EHS-KB 服务并设为自动启动
#        → 开放 8080 端口 → 注册「EHS知识库每日备份」计划任务（每天 01:30）

# 常用运维
nssm status  EHS-KB
nssm restart EHS-KB
nssm edit    EHS-KB          # 图形界面改环境变量（口令、端口、目录）
type D:\ehs\app\logs\service.log
```

手工注册服务（等价命令）：

```powershell
D:\ehs\app\.venv\Scripts\pip.exe install -r D:\ehs\app\requirements.txt
nssm install EHS-KB "D:\ehs\app\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 0.0.0.0 --port 8080"
nssm set EHS-KB AppDirectory D:\ehs\app
nssm set EHS-KB AppEnvironmentExtra "EHS_DOCS_ROOT=D:\ehs\docs" "EHS_ADMIN_TOKEN=你的口令" "EHS_PORT=8080"
nssm set EHS-KB Start SERVICE_AUTO_START
nssm start EHS-KB
New-NetFirewallRule -DisplayName "EHS-KB 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
# 访问 http://<内网IP>:8080 ，用 ipconfig 查 IP
```

## 六、备份

| 平台 | 脚本 | 默认行为 |
| --- | --- | --- |
| Linux | `deploy/backup.sh` | 打包 `/srv/ehs/docs` → `/backup/ehs/ehs-docs-<时间戳>.tar.gz`，保留 30 份 |
| Windows | `deploy\backup.bat` | 压缩 `D:\ehs\docs` → `\\NAS\backup\ehs\ehs-docs-<时间戳>.zip`，保留 30 天 |

改备份路径：直接编辑脚本顶部的 `BACKUP_DIR`（或设环境变量 `EHS_BACKUP_DIR`）。
备份包含 `.trash` 与 `operation.log`，所以**整库恢复 = 解压覆盖 docs 目录**，无需任何数据库还原。

## 七、日常使用

| 场景 | 操作 |
| --- | --- |
| 新增一篇知识 | 往分类目录丢一个 `.md`（或管理视图「新建文档」）→ 页面 8 秒内自动出现 |
| 加附件 | 在 `.md` 旁建同名文件夹，把 PDF/图片/Excel 丢进去 |
| 下线一篇 | 把 frontmatter 的 `status` 改成 `废止`（保留可查），或直接删文件（进回收站） |
| 找东西 | 顶部搜索框，支持中文分词；左侧「标签」页签按 tag 过滤 |
| 看快到期的 | 顶部「到期提醒」按钮，红色=已逾期，橙色=7 天内，黄色=30 天内 |
| 进管理视图 | 顶部「管理视图」→ 输入 `EHS_ADMIN_TOKEN` 口令 + 你的姓名（记入日志） |
| 误删恢复 | 管理面板 →「回收站」→ 还原（90 天内） |

## 八、接口一览

| 方法 | 路径 | 说明 | 需口令 |
| --- | --- | --- | --- |
| GET | `/api/stats` `/api/tree` `/api/tags` | 概览 / 目录树 / 标签 | 否 |
| GET | `/api/docs?category=&tag=&status=` | 条件列表 | 否 |
| GET | `/api/search?q=&tag=&category=` | 全文检索（带高亮片段） | 否 |
| GET | `/api/reviews?days=30` | 到期提醒 | 否 |
| GET | `/api/doc?path=…` | 单篇（元数据 + HTML + 附件） | 否 |
| GET | `/api/asset/<相对路径>?download=1` | 附件 / 图片 | 否 |
| POST | `/api/admin/login` | 校验口令 | — |
| POST | `/api/admin/save` | 新建 / 编辑 frontmatter 与正文 | 是 |
| POST | `/api/admin/delete` `/restore` | 移入回收站 / 还原 | 是 |
| POST | `/api/admin/upload` `/mkdir` `/reindex` | 上传 / 建分类 / 重建索引 | 是 |
| GET | `/api/admin/log` `/trash` `/errors` | 操作日志 / 回收站 / 解析异常 | 是 |

管理接口用请求头 `X-Admin-Token: <口令>` 认证，`X-Admin-User: <姓名的 URL 编码>` 标记操作人。

## 九、安全边界（内网自用的取舍）

- 口令走请求头明文比对（`secrets.compare_digest` 防时序侧信道），**仅适用于可信内网**；
  若要跨网段暴露，请在前面套 Nginx + HTTPS + IP 白名单。
- 所有路径参数经 `safepath.py` 规范化，`..`、盘符、越界一律 400，已实测。
- 上传按后缀白名单放行（见 `config.py` 的 `allowed_suffixes`），单文件默认上限 100 MB。
- 不设登录态、不写 Cookie，同事只读浏览无任何门槛。
