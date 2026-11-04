"""飞常准（VariFlight）MCP 爬虫

通过飞常准官方 MCP 网关获取真实票价，需要在 .env 配置 VARIFLIGHT_MCP_KEY。
- 网关：https://c-gw.variflight.com/chat_message/mcp/api
- 鉴权：Authorization: Bearer <MCP Key>
- 每个 Key 含 100 次免费调用，之后按官方商业方案计费。

未配置 Key 时 search() 返回空列表并给出一次性提示，不影响飞猪主数据源。
"""
import json
import os

import requests

from app.airlines import classify, baggage_text

GATEWAY = "https://c-gw.variflight.com/chat_message/mcp/api"
PROTOCOL_VERSION = "2025-03-26"
_TIMEOUT = 20


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

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, payload: dict) -> dict:
        try:
            resp = requests.post(
                GATEWAY, headers=self._headers, json=payload, timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise VariflightError(f"network error: {exc}") from exc

        if resp.status_code == 401:
            raise VariflightError("401 unauthorized: MCP Key 无效或已过期")
        if resp.status_code >= 400:
            raise VariflightError(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            return self._parse_sse(resp.text)
        try:
            return resp.json()
        except ValueError as exc:
            raise VariflightError(f"bad JSON response: {resp.text[:200]}") from exc

    @staticmethod
    def _parse_sse(text: str) -> dict:
        """解析 SSE 响应，返回最后一个 JSON-RPC 消息。"""
        messages = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data and data != "[DONE]":
                    try:
                        messages.append(json.loads(data))
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


def _extract_text(result: dict) -> str:
    """从 tools/call 结果中拼接文本内容。"""
    parts = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def _extract_structured(result: dict):
    """从 resource / text 块中提取结构化数据（dict/list）。"""
    for block in result.get("content", []):
        if block.get("type") == "resource":
            resource = block.get("resource", {})
            text = resource.get("text")
            if text:
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
        if block.get("type") == "text":
            text = block.get("text", "")
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


class VariflightCrawler:
    """飞常准票价爬虫，接口与 FliggyCrawler 对齐。"""

    name = "variflight"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key if api_key is not None else os.getenv(
            "VARIFLIGHT_MCP_KEY", ""
        )
        self._warned = False

    def search(self, dep_code: str, arr_code: str, dep_date: str,
               ret_date: str | None = None) -> list[dict]:
        if not self._api_key:
            if not self._warned:
                print("[variflight] 未配置 VARIFLIGHT_MCP_KEY，跳过该数据源")
                self._warned = True
            return []

        client = VariflightMCPClient(self._api_key)
        try:
            return self._route_flights(client, dep_code, arr_code, dep_date)
        except VariflightError as exc:
            print(f"[variflight] {dep_date} 调用失败: {exc}")
            return []
        except Exception as exc:  # 兜底：任何异常都不能拖垮主流程
            print(f"[variflight] {dep_date} 未知错误: {exc}")
            return []

    def _route_flights(self, client: VariflightMCPClient,
                       dep_code: str, arr_code: str, dep_date: str) -> list[dict]:
        """实时票价：getRouteFlightTickets（DuckDB SQL）。"""
        sql = (
            "SELECT o.option_id, o.adult_total, o.transfers, o.legs "
            "FROM flights("
            "market := 'domestic', "
            f"legs := [{{'dep_city':'{dep_code.upper()}',"
            f"'arr_city':'{arr_code.upper()}','date':'{dep_date}'}}], "
            "cabin := 'economy') AS o "
            "WHERE o.adult_total IS NOT NULL "
            "ORDER BY o.adult_total, o.option_id LIMIT 20"
        )
        result = client.call_tool("getRouteFlightTickets", {"sql": sql})
        data = _extract_structured(result)
        flights = self._parse_route(data, dep_code, arr_code)

        if flights:
            print(
                f"[variflight] got {len(flights)} flights, "
                f"cheapest: ¥{flights[0]['price']}"
            )
        else:
            # 详细接口无结果时，回退到每日最低价日历
            flights = self._lowest_price(
                client, dep_code, arr_code, dep_date
            )
        return flights

    def _lowest_price(self, client: VariflightMCPClient,
                      dep_code: str, arr_code: str, dep_date: str) -> list[dict]:
        """航线最低价日历：getFlightTicketPrice（仅当日最低价）。"""
        result = client.call_tool("getFlightTicketPrice", {
            "dep": dep_code.upper(),
            "arr": arr_code.upper(),
            "type": "1",
            "depDateBegin": dep_date,
            "depDateEnd": dep_date,
        })
        data = _extract_structured(result)
        price = self._find_lowest_price(data, dep_date)
        if price is None:
            return []
        print(f"[variflight] calendar lowest on {dep_date}: ¥{price}")
        return [{
            "platform": "飞常准",
            "price": int(price),
            "flight_no": "",
            "airline": "",
            "airline_category": "",
            "baggage_text": "",
            "dep_time": "",
            "arr_time": "",
            "dep_airport": "",
            "arr_airport": "",
            "is_direct": True,
            "stops": 0,
            "link": "",
        }]

    @staticmethod
    def _parse_route(data, dep_code: str, arr_code: str) -> list[dict]:
        """容错解析 getRouteFlightTickets 的结构化结果。"""
        if data is None:
            return []
        rows = data
        if isinstance(data, dict):
            for key in ("rows", "data", "results", "records"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
            else:
                rows = [data]
        if not isinstance(rows, list):
            return []

        flights = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = row.get("adult_total")
            if price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue

            airline = VariflightCrawler._pick_airline(row)
            flight_no = VariflightCrawler._pick_flight_no(row)
            dep_time, arr_time = VariflightCrawler._pick_times(row)
            transfers = row.get("transfers", 0)
            try:
                stops = int(transfers or 0)
            except (TypeError, ValueError):
                stops = 0

            flights.append({
                "platform": "飞常准",
                "price": int(price),
                "flight_no": flight_no,
                "airline": airline,
                "airline_category": classify(airline) if airline else "",
                "baggage_text": baggage_text(airline) if airline else "",
                "dep_time": dep_time,
                "arr_time": arr_time,
                "dep_airport": dep_code.upper(),
                "arr_airport": arr_code.upper(),
                "is_direct": stops == 0,
                "stops": stops,
                "link": "",
            })

        flights.sort(key=lambda x: x["price"])
        return flights

    @staticmethod
    def _pick_airline(row: dict) -> str:
        for key in ("airline_name", "airline", "marketing_airline", "carrier"):
            val = row.get(key)
            if isinstance(val, str) and val:
                return val
        legs = row.get("legs")
        name = VariflightCrawler._dig_legs(legs, "airline_name")
        return name or ""

    @staticmethod
    def _pick_flight_no(row: dict) -> str:
        for key in ("flight_no", "flight_number", "fnum"):
            val = row.get(key)
            if isinstance(val, str) and val:
                return val
        return VariflightCrawler._dig_legs(row.get("legs"), "flight_number") or ""

    @staticmethod
    def _pick_times(row: dict) -> tuple[str, str]:
        dep = row.get("departure") or row.get("dep_time") or ""
        arr = row.get("arrival") or row.get("arr_time") or ""
        if not dep:
            dep = VariflightCrawler._dig_legs(row.get("legs"), "departure") or ""
        if not arr:
            arr = VariflightCrawler._dig_legs(row.get("legs"), "arrival") or ""
        return str(dep), str(arr)

    @staticmethod
    def _dig_legs(legs, key: str) -> str:
        """在 legs/segments 嵌套结构里找第一个字符串值。"""
        if not isinstance(legs, list):
            return ""
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            val = leg.get(key)
            if isinstance(val, str) and val:
                return val
            segments = leg.get("segments")
            if isinstance(segments, list):
                for seg in segments:
                    if isinstance(seg, dict):
                        val = seg.get(key)
                        if isinstance(val, str) and val:
                            return val
        return ""

    @staticmethod
    def _find_lowest_price(data, dep_date: str):
        """在最低价日历的多种可能结构中找当日价格。"""
        if data is None:
            return None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    d = item.get("date") or item.get("depDate")
                    p = item.get("price") or item.get("lowestPrice")
                    if d == dep_date and p is not None:
                        return p
            # 只有一条时直接取
            if len(data) == 1 and isinstance(data[0], dict):
                p = data[0].get("price") or data[0].get("lowestPrice")
                if p is not None:
                    return p
        if isinstance(data, dict):
            for key in ("price", "lowestPrice", "minPrice", "totalPrice"):
                val = data.get(key)
                if val is not None:
                    return val
            for key in ("data", "list", "items", "calendar"):
                if key in data:
                    found = VariflightCrawler._find_lowest_price(
                        data[key], dep_date
                    )
                    if found is not None:
                        return found
        return None
