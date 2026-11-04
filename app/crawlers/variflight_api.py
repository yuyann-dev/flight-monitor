"""飞常准（VariFlight）MCP 爬虫

通过飞常准官方 MCP 网关获取真实票价，需要在 .env 配置 VARIFLIGHT_MCP_KEY。
- 网关：https://c-gw.variflight.com/chat_message/mcp/api
- 鉴权：Authorization: Bearer <MCP Key>
- 每个 Key 含 100 次免费调用，之后按官方商业方案计费。

未配置 Key 时 search() 返回空列表并给出一次性提示，不影响飞猪主数据源。
"""
import json
import os
import time
from pathlib import Path

import requests

from app.airlines import classify, baggage_text

GATEWAY = "https://c-gw.variflight.com/chat_message/mcp/api"
PROTOCOL_VERSION = "2025-03-26"
_TIMEOUT = 20
_THROTTLE_FILE = Path(__file__).resolve().parent.parent.parent / (
    "data" + os.sep + ".variflight_last_call"
)


class VariflightError(Exception):
    """飞常准调用失败（鉴权、网络、协议错误）。"""


class VariflightMCPClient:
    """最小可用的 MCP Streamable HTTP 客户端（JSON-RPC 2.0）。"""

    def __init__(self, api_key: str):
        if not api_key:
            raise VariflightError("missing VARIFLIGHT_MCP_KEY")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        self._req_id = 0
        self._initialized = False
        self._session_id: str | None = None

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, payload: dict) -> dict:
        # initialize 之后服务端会下发 Mcp-Session-Id，后续请求必须原样带回
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            resp = requests.post(
                GATEWAY, headers=headers, json=payload, timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise VariflightError(f"network error: {exc}") from exc

        # SSE 响应常不带 charset，requests 会回退 latin-1 导致中文乱码，强制 UTF-8
        resp.encoding = "utf-8"

        if resp.status_code == 401:
            raise VariflightError("401 unauthorized: MCP Key 无效或已过期")
        if resp.status_code >= 400:
            raise VariflightError(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

        # 保存服务端分配/更新的会话 ID
        new_session = resp.headers.get("Mcp-Session-Id")
        if new_session:
            self._session_id = new_session

        # 通知类请求（无 id，如 notifications/initialized）服务端返回空响应体
        if not resp.text.strip():
            return {}

        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            return self._parse_sse(resp.text)
        try:
            return resp.json()
        except ValueError as exc:
            raise VariflightError(f"bad JSON response: {resp.text[:200]}") from exc

    @staticmethod
    def _parse_sse(text: str) -> dict:
        """解析 SSE 响应，返回最后一个 JSON-RPC 消息。

        兼容标准 SSE（多个 data: 行）以及该网关把长 JSON 物理折行、
        延续行不带 data: 前缀的情况：按空行划分事件，data: 行取前缀后内容，
        事件内其余非 id:/event: 的非空行视为 data 的延续，直接拼接。
        """
        messages = []
        for block in text.split("\n\n"):
            parts = []
            collecting = False
            for raw_line in block.splitlines():
                line = raw_line.strip()
                if line.startswith("data:"):
                    collecting = True
                    parts.append(line[len("data:"):].strip())
                elif line.startswith(("id:", "event:", "retry:", ":")):
                    continue
                elif collecting and line:
                    parts.append(line)
            if parts:
                joined = "".join(parts)
                if joined and joined != "[DONE]":
                    try:
                        messages.append(json.loads(joined))
                    except json.JSONDecodeError:
                        continue
        if not messages:
            raise VariflightError("empty SSE response")
        return messages[-1]

    def initialize(self) -> None:
        if self._initialized:
            return
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "flight-monitor", "version": "1.0"},
            },
        })
        if "error" in result:
            raise VariflightError(f"initialize failed: {result['error']}")
        # 通知服务端初始化完成（无 id 的通知，无响应体也可接受）
        self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        self._initialized = True

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.initialize()
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if "error" in result:
            raise VariflightError(f"tool {name} error: {result['error']}")
        return result.get("result", {})



class VariflightCrawler:
    """飞常准票价爬虫，接口与 FliggyCrawler 对齐。"""

    name = "variflight"

    def __init__(self, api_key: str | None = None,
                 min_interval_hours: float = 0.0):
        self._api_key = api_key if api_key is not None else os.getenv(
            "VARIFLIGHT_MCP_KEY", ""
        )
        self._min_interval = (
            float(min_interval_hours) * 3600.0
            if min_interval_hours else 0.0
        )
        self._warned = False
        self._throttled_warned = False
        # True=链路成功响应；False=调用报错；None=未尝试（未配置/节流跳过）
        self.last_ok = None

    def _throttle_allows(self) -> bool:
        """基于文件时间戳判断是否到达允许调用的间隔。"""
        if self._min_interval <= 0:
            return True
        try:
            if _THROTTLE_FILE.exists():
                last = float(_THROTTLE_FILE.read_text().strip())
                if time.time() - last < self._min_interval:
                    return False
        except (ValueError, OSError):
            # 状态文件损坏时不阻塞，按允许处理
            return True
        return True

    def _record_call(self) -> None:
        """记录本次调用时间戳。"""
        if self._min_interval <= 0:
            return
        try:
            _THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _THROTTLE_FILE.write_text(str(time.time()))
        except OSError:
            pass

    def search(self, dep_code: str, arr_code: str, dep_date: str,
               ret_date: str | None = None) -> list[dict]:
        self.last_ok = None
        if not self._api_key:
            if not self._warned:
                print("[variflight] 未配置 VARIFLIGHT_MCP_KEY，跳过该数据源")
                self._warned = True
            return []

        if not self._throttle_allows():
            if not self._throttled_warned:
                print("[variflight] 处于节流间隔内，本轮跳过以节省额度")
                self._throttled_warned = True
            return []

        client = VariflightMCPClient(self._api_key)
        try:
            result = self._route_flights(client, dep_code, arr_code, dep_date)
            self._record_call()
            self.last_ok = True
            return result
        except VariflightError as exc:
            print(f"[variflight] {dep_date} 调用失败: {exc}")
            self.last_ok = False
            return []
        except Exception as exc:  # 兜底：任何异常都不能拖垮主流程
            print(f"[variflight] {dep_date} 未知错误: {exc}")
            self.last_ok = False
            return []

    def _route_flights(self, client: VariflightMCPClient,
                       dep_code: str, arr_code: str, dep_date: str) -> list[dict]:
        """真实票价：getFlightList 补全时间/机场/航司，getRoutePriceData
        提供真实报价，两者按航班号关联，仅保留有真实报价的航班。"""
        dep, arr, date = dep_code.upper(), arr_code.upper(), dep_date

        # 1. 完整航班摘要（计划起降时间、机场、航司中文名、距离等）
        list_result = client.call_tool("getFlightList", {
            "date": date, "dep": dep, "arr": arr,
        })
        flight_items = list_result.get("structuredContent", {}).get("items", [])
        info_by_fnum = {f["fnum"]: f for f in flight_items if f.get("fnum")}

        # 2. 票价列表（核心，仅保留有真实报价的航班）
        price_result = client.call_tool("getRoutePriceData", {
            "dep": dep, "arr": arr, "startDate": date, "endDate": date,
        })
        tickets = (
            price_result.get("structuredContent", {})
            .get("item", {}).get("tickets", [])
        )

        flights = []
        for t in tickets:
            fnum = t.get("fnum", "")
            try:
                price = int(float(t.get("price")))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            info = info_by_fnum.get(fnum, {})
            airline = info.get("airlineCnName") or t.get("airlineName", "")
            flights.append({
                "platform": "飞常准",
                "price": price,
                "flight_no": fnum,
                "airline": airline,
                "airline_category": classify(airline) if airline else "",
                "baggage_text": baggage_text(airline) if airline else "",
                "dep_time": info.get("departurePlanTime", ""),
                "arr_time": info.get("arrivalPlanTime", ""),
                "dep_airport": info.get("depAirport", ""),
                "arr_airport": info.get("arrAirport", ""),
                # getFlightList 返回的是单个航班，不含中转组合，按直飞处理
                "is_direct": True,
                "stops": 0,
                # 无独立购票链接时前端兜底为飞常准官网
                "link": "",
            })
        flights.sort(key=lambda x: x["price"])
        return flights
