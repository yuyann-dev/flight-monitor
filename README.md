# 机票价格监控系统

面向个人使用的单程机票价格监控与低价提醒系统：定时抓取真实航班价格，结合时间阶段、历史价格位置和趋势给出购买建议，达到低价时自动发邮件提醒。

## 功能

- 真实航班数据：通过飞猪官方 `flyai` 命令行工具查询，无需浏览器与登录态
- 邮箱验证码登录，低价自动邮件提醒（含防打扰冷却机制）
- 购买建议综合时间阶段、历史价格位置、趋势预测和用户心理价位
- 支持直飞、出发/到达时段、免费托运行李等筛选，支持手动剔除不关注的航班
- 机建燃油费用自动计算，含税总价清晰展示
- 单页前端，手机与电脑均适配

## 技术栈

- 后端：FastAPI + SQLAlchemy + SQLite + APScheduler
- 数据采集：飞猪 `flyai` CLI（subprocess 调用）
- 前端：原生 HTML / JavaScript（Chart.js 绘制价格走势）

## 目录结构

```
app/
  main.py          FastAPI 路由
  config.py        环境配置
  models.py        数据模型
  auth.py          验证码登录与会话
  analyzer.py      价格分析与购买建议
  airlines.py      航司分类与行李规则（唯一权威来源）
  scheduler.py     定时调度与样本落库
  email_service.py 邮件发送
  crawlers/
    fliggy_api.py  flyai CLI 爬虫
static/index.html  单页前端
```

## 部署

```bash
cd /opt/flight-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 安装并登录飞猪 flyai CLI
cp .env.example .env  # 填入密钥与邮箱配置
systemctl enable --now flight-monitor
```

Nginx 反向代理：

```nginx
location /flight/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 说明

- `flyai` 免费版可查询约 45-50 天内的航班，超出范围的日期会持续监控，到可查询时自动抓取。
- 价格建议为基于历史数据的统计判断，实际价格受节假日、供需等因素影响，仅供参考。
