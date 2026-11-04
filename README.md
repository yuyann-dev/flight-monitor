# 机票价格监控系统

面向个人及少量亲友使用的单程机票价格监控与低价提醒系统。定时抓取真实航班价格，结合时间阶段、历史价格位置、变动趋势和用户心理价位给出通俗易懂的购买建议，并在达到低价时自动发送邮件提醒。系统不使用任何模拟或示例数据，所有价格均来自真实查询。

## 功能特性

- **多数据源架构**：默认使用飞猪官方 `flyai` 命令行工具；可选接入飞常准 MCP 作为第二数据源，未配置密钥时自动跳过，不影响主流程
- **真实票价采集**：无需浏览器与登录态，按航线、日期定时查询，自动去重并记录价格样本
- **多维购买建议**：综合时间阶段、历史价格位置、趋势和心理价位；数据较少时以行业规则为主、自有数据为辅，任何阶段都能给出判断，不设置"数据缓冲期"
- **灵活筛选**：支持直飞、出发/到达时段（两者同时填写时取并集）、免费托运行李等条件，并可手动剔除不关注的航班
- **真实行李信息**：按航司分类（全服务 / 差异化 / 廉航）生成免费托运行李说明，不臆造字段
- **邮件低价提醒**：含防打扰冷却时间，二次提醒需价格再下降一定比例
- **费用计算**：机建费与燃油附加费自动计算，含税总价清晰展示
- **响应式单页前端**：手机与电脑均适配，支持浏览器历史导航，移动端返回键可在详情页与列表间正常切换

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI、SQLAlchemy、SQLite、APScheduler |
| 数据采集 | 飞猪 `flyai` CLI（subprocess）；飞常准 MCP（JSON-RPC over HTTP，可选） |
| 认证 | 邮箱验证码 + JWT 会话 |
| 前端 | 原生 HTML / JavaScript，Chart.js 绘制价格走势 |
| 部署 | Uvicorn + systemd + Nginx 反向代理 |

## 目录结构

```
flight-monitor/
├── app/
│   ├── main.py              # FastAPI 路由与接口
│   ├── config.py            # 环境变量配置
│   ├── models.py            # SQLAlchemy 数据模型
│   ├── auth.py              # 验证码登录与 JWT 会话
│   ├── analyzer.py          # 价格分析与购买建议
│   ├── airlines.py          # 航司分类与行李规则（唯一权威来源）
│   ├── scheduler.py         # 定时调度、多源合并与样本落库
│   ├── email_service.py     # 邮件发送
│   └── crawlers/
│       ├── fliggy_api.py    # 飞猪 flyai CLI 爬虫
│       └── variflight_api.py# 飞常准 MCP 爬虫（可选）
├── static/
│   └── index.html           # 单页前端
├── data/                    # SQLite 数据库与运行状态（不入库）
├── requirements.txt
├── .env.example
└── README.md
```

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env         # 填写密钥、邮箱等配置
# 安装并登录飞猪 flyai CLI（主数据源）
```

可选启用飞常准：在 `.env` 中填写 `VARIFLIGHT_MCP_KEY`，并通过
`VARIFLIGHT_MIN_INTERVAL_HOURS` 控制最小调用间隔以节省额度；留空则仅使用飞猪。

## 生产部署

通过 systemd 托管服务：

```bash
systemctl enable --now flight-monitor
```

Nginx 反向代理示例：

```nginx
location /flight/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## 使用说明与限制

- 飞猪 `flyai` 免费版可查询约 45–50 天内的航班，超出范围的日期会持续监控，进入可查询区间后自动抓取。
- 飞常准 MCP 每个密钥通常含有限的免费调用次数，之后按官方方案计费，因此默认提供调用间隔节流能力。
- 购买建议为基于历史数据的统计判断，实际价格受节假日、供需、航司政策等因素影响，仅供参考，不构成购票承诺。
