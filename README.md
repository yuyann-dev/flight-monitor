# ✈️ Flight Monitor

多平台机票价格监控与低价提醒系统。

## 功能

- 携程 / 去哪儿 / 飞猪 / 同程 四平台实时比价
- 邮箱验证码登录，自动发送低价提醒邮件
- 价格百分位 + EWMA 趋势预测 + 行业先验规则综合判断入手时机
- 机建燃油自动计算，含税总价一目了然
- 出发/到达时段筛选、直飞偏好、托运行李筛选
- 一键停止监控

## 技术栈

- FastAPI + SQLAlchemy + SQLite
- Playwright 无头浏览器爬虫
- APScheduler 定时调度
- 原生 HTML/JS 前端

## 部署

```bash
cd /opt/flight-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # 填入配置
systemctl enable --now flight-monitor
```

Nginx 反代：
```nginx
location /flight/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```
