"""本文件以少量串行请求探测免费数据源，保存原始证据；默认仅预览，不执行股票同步。"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo


STATE_DIR = Path(__file__).resolve().parents[1] / ".source-probe"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MIN_INTERVAL = 10.0
PER_SOURCE_DAILY_LIMIT = 20
TOTAL_DAILY_LIMIT = 30
MAX_RUN_REQUESTS = 10
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Probe:
    """描述单个请求及其最小结构检查；通过不等于全市场覆盖已通过。"""

    id: str
    source: str
    group: str
    url: str
    shape: str
    board: str | None = None


def endpoint(base: str, **params: str) -> str:
    """只构造公开接口地址，不发送请求。"""
    return base + "?" + urlencode(params)


def make_plan(target: date, bse_code: str | None = None) -> list[Probe]:
    """根据已确认的目标交易日生成有限样本；北交所代码从目录响应发现。"""
    samples = {
        "SSE_MAIN": ("sh", "600000"), "SZSE_MAIN": ("sz", "000001"),
        "CHINEXT": ("sz", "300750"), "STAR": ("sh", "688981"),
        "BSE": ("bj", bse_code or "BSE_CODE"),
    }
    filters = {
        "BSE": "m:0 t:81 s:2048", "SSE_MAIN": "m:1 t:2",
        "SZSE_MAIN": "m:0 t:6", "CHINEXT": "m:0 t:80", "STAR": "m:1 t:23",
    }
    clist = "https://82.push2.eastmoney.com/api/qt/clist/get"
    result = []
    # 1. 只取每板块第一页五条，不调用第三方封装中的自动翻页循环。
    for board, market_filter in filters.items():
        result.append(Probe(f"em_catalog_{board}", "eastmoney", "catalog", endpoint(
            clist, pn="1", pz="5", po="1", np="1", fltt="2", invt="2",
            fid="f12", fs=market_filter,
            fields="f12,f13,f14,f2,f5,f6,f8,f20,f124",
        ), "em_catalog", board))
    result.append(Probe("sina_catalog", "sina", "catalog", endpoint(
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
        page="1", num="5", sort="symbol", asc="1", node="hs_a", symbol="",
    ), "sina_catalog"))
    # 2. 不复权与前复权分别取有限历史，用于后续核查日期、单位和复权差异。
    begin = target - timedelta(days=400)
    for board, (prefix, code) in samples.items():
        for adjustment, fqt in (("raw", "0"), ("qfq", "1")):
            result.append(Probe(f"em_history_{board}_{adjustment}", "eastmoney", "history", endpoint(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                fields1="f1,f2,f3,f4,f5,f6", fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                klt="101", fqt=fqt, secid=f"{1 if prefix == 'sh' else 0}.{code}",
                beg=begin.strftime("%Y%m%d"), end=target.strftime("%Y%m%d"),
            ), "em_history", board))
        result.append(Probe(f"tencent_history_{board}", "tencent", "history", endpoint(
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            param=f"{prefix}{code},day,{begin},{target},640,",
        ), "tencent_history", board))
        result.append(Probe(f"sina_financial_{board}", "sina", "financial", endpoint(
            "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022",
            paperCode=f"{prefix}{code}", source="lrb", type="0", page="1", num="8",
        ), "sina_financial", board))
    # 3. 复权因子和分类成员均只探测一个样本；不能据此认定全市场覆盖。
    result.append(Probe("sina_qfq_factor", "sina", "factors",
                        "https://finance.sina.com.cn/realstock/company/sh600000/qfq.js", "sina_factor"))
    for kind, host, market_filter, sample_board in (
        ("industry", "17", "m:90 t:2 f:!50", "BK1027"),
        ("concept", "79", "m:90 t:3 f:!50", "BK0715"),
    ):
        for suffix, host_number, fs in (("list", host, market_filter), ("members", "29", f"b:{sample_board} f:!50")):
            result.append(Probe(f"em_{kind}_{suffix}", "eastmoney", "classification", endpoint(
                f"https://{host_number}.push2.eastmoney.com/api/qt/clist/get",
                pn="1", pz="5", po="1", np="1", fltt="2", invt="2", fid="f12", fs=fs,
                fields="f12,f13,f14",
            ), "em_catalog"))
    return result


class Deferred(Exception):
    """预算、冷却或运行上限使请求留待后续执行。"""


class Guard:
    """保存全渠道间隔、每日预算和渠道冷却；外层文件锁保证单进程串行。"""

    def __init__(self, directory: Path, now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = lambda: random.uniform(0, 3)) -> None:
        self.directory = directory
        self.now, self.sleep, self.jitter = now, sleep, jitter
        self.path = directory / "state.json"
        # 损坏状态不能静默清空，否则重启会绕过已消耗的预算。
        self.state: dict[str, Any] = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.run_count = 0

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.path)

    def reserve(self, source: str) -> None:
        """每个实际请求在发送前记账，失败和重启也不能免除预算。"""
        if self.run_count >= MAX_RUN_REQUESTS:
            raise Deferred("达到单轮 10 次请求上限")
        if self.state.get("cooldown", {}).get(source, 0) > self.now():
            raise Deferred("渠道处于冷却期")
        delay = max(0.0, self.state.get("next_at", 0) - self.now())
        if delay:
            self.sleep(delay)
        day = datetime.fromtimestamp(self.now(), SHANGHAI).date().isoformat()
        counts = self.state.setdefault("daily", {}).setdefault(day, {})
        if counts.get(source, 0) >= PER_SOURCE_DAILY_LIMIT or sum(counts.values()) >= TOTAL_DAILY_LIMIT:
            raise Deferred("达到渠道每日 20 次或全部渠道每日 30 次上限")
        counts[source] = counts.get(source, 0) + 1
        self.state["next_at"] = self.now() + MIN_INTERVAL + self.jitter()
        self.run_count += 1
        self.save()

    def cooldown(self, source: str, seconds: float) -> None:
        blocked = self.state.setdefault("cooldown", {})
        blocked[source] = max(blocked.get(source, 0), self.now() + seconds)
        self.save()


class NoRedirect(HTTPRedirectHandler):
    """不自动跟随重定向，避免隐含请求绕过预算或访问验证页面。"""

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def retry_delay(value: str | None, now: float) -> float:
    """限流至少冷却 24 小时，尊重更长的 Retry-After。"""
    seconds = 0.0
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - now
            except (ValueError, TypeError, OverflowError):
                pass
    return max(86400.0, seconds)


def inspect_payload(probe: Probe, body: str, target: date) -> dict[str, Any]:
    """仅校验有业务数据且结构基本可读；单位、覆盖与复权仍须人工核验。"""
    if body.lstrip().lower().startswith(("<!doctype", "<html")):
        raise ValueError("返回 HTML，可能为拦截或验证页面")
    if probe.shape == "sina_factor":
        # 新浪返回 JS 赋值；只解析 JSON，不执行 eval 或远程 JavaScript。
        body = body[body.index("=") + 1:].strip().rstrip(";")
    payload = json.loads(body)
    summary: dict[str, Any] = {"quality": "sample_only"}
    dates: list[str] = []
    if probe.shape == "em_catalog":
        data = payload["data"]
        rows = data["diff"]
        if isinstance(rows, dict):
            rows = list(rows.values())
        summary["sourceTotal"] = data.get("total")
        summary["codes"] = [row["f12"] for row in rows]
        if any(not row.get("f12") or not row.get("f14") for row in rows):
            raise ValueError("目录缺少代码或名称")
    elif probe.shape == "em_history":
        rows = [row.split(",") for row in payload["data"]["klines"]]
        if any(len(row) < 11 for row in rows):
            raise ValueError("日线字段数量不足")
        dates = [row[0] for row in rows]
    elif probe.shape == "tencent_history":
        entry = next(iter(payload["data"].values()))
        rows = entry.get("day", [])
        if any(len(row) < 6 for row in rows):
            raise ValueError("腾讯日线字段数量不足")
        dates = [row[0] for row in rows]
        summary["unitCheck"] = "第六列单位及成交额覆盖待核验，不能直接映射 amount"
    elif probe.shape == "sina_financial":
        reports = payload["result"]["data"]["report_list"]
        rows = list(reports.values())
        if any(not row.get("data") for row in rows):
            raise ValueError("财报条目为空")
        summary["reportPeriods"] = list(reports)
        summary["accountingCheck"] = "累计/单季、合并/母公司、币种、金额单位与归母净利润待核验"
    elif probe.shape == "sina_factor":
        rows = payload["data"]
        summary["adjustmentCheck"] = "因子方向、锚定日期与除权样本待核验"
    else:
        rows = payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("没有可核验的业务记录；不据此推断渠道不支持")
    summary["rows"] = len(rows)
    if dates:
        parsed = [date.fromisoformat(value) for value in dates]
        summary.update({"earliestDate": min(dates), "latestDate": max(dates),
                        "reachesTarget": max(parsed) == target})
        if max(parsed) > target:
            raise ValueError("返回目标日期以后的行情")
    return summary


def fetch(probe: Probe, guard: Guard, target: date, opener: Any = None) -> dict[str, Any]:
    """单请求、无自动重试；限流、网络失败和解析失败均保留结果并冷却渠道。"""
    record: dict[str, Any] = {"id": probe.id, "source": probe.source, "board": probe.board, "url": probe.url,
                              "checkedAt": datetime.fromtimestamp(guard.now(), SHANGHAI).isoformat()}
    key = hashlib.sha256(probe.url.encode()).hexdigest()
    evidence = guard.directory / f"{key}.txt"
    try:
        # 1. 同一请求的成功响应保存 24 小时，重复验证直接读取证据。
        cached = evidence.exists() and 0 <= guard.now() - evidence.stat().st_mtime < 86400
        if cached:
            body = evidence.read_text()
        else:
            guard.reserve(probe.source)
            request = Request(probe.url, headers={"User-Agent": "StockSieve-Source-Probe/0.1", "Accept": "application/json,text/plain"})
            with (opener or build_opener(NoRedirect())).open(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("响应超过 4 MiB 限额")
                body = raw.decode(response.headers.get_content_charset() or "utf-8")
        # 2. 有效结构才保存为可复用证据；HTTP 200 本身不视为成功。
        summary = inspect_payload(probe, body, target)
        if not cached:
            evidence.write_text(body)
        record.update(status="sample_received", cached=cached, summary=summary,
                      evidence=evidence.name, sha256=hashlib.sha256(body.encode()).hexdigest())
    except Deferred as error:
        record.update(status="deferred", reason=str(error))
    except HTTPError as error:
        limited = error.code in (403, 418, 429)
        guard.cooldown(probe.source, retry_delay(error.headers.get("Retry-After"), guard.now()) if limited else 3600)
        record.update(status="http_error", httpStatus=error.code, reason=str(error))
    except (URLError, TimeoutError, OSError) as error:
        guard.cooldown(probe.source, 3600)
        record.update(status="network_error", reason=str(error))
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration) as error:
        guard.cooldown(probe.source, 86400)
        record.update(status="unverified_response", reason=str(error))
    return record


def main() -> int:
    """预览探测计划，或在持有进程锁时执行有限的联网验证。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="人工确认的已收盘交易日 YYYY-MM-DD")
    parser.add_argument("--source", choices=("sina", "eastmoney", "tencent"))
    parser.add_argument("--group", choices=("catalog", "history", "classification", "financial", "factors"))
    parser.add_argument("--bse-code", help="已核对的当前北交所六位代码；省略时从目录样本发现")
    parser.add_argument("--execute", action="store_true", help="实际联网；省略只打印计划")
    args = parser.parse_args()
    if args.date > datetime.now(SHANGHAI).date():
        parser.error("目标日期不能在未来")
    if args.bse_code and not re.fullmatch(r"\d{6}", args.bse_code):
        parser.error("北交所代码须为六位数字，市场身份需另行核对")
    probes = [probe for probe in make_plan(args.date, args.bse_code)
              if (not args.source or probe.source == args.source) and (not args.group or probe.group == args.group)]
    if not probes:
        parser.error("所选渠道没有该分组的探测项")
    if not args.execute:
        print(json.dumps({"mode": "preview", "requests": len(probes), "probes": [asdict(probe) for probe in probes]}, ensure_ascii=False, indent=2))
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("已有探测进程运行，请等待结束")
        guard = Guard(STATE_DIR)
        discovered = guard.state.get("bse_sample", {})
        bse_code = args.bse_code or (discovered.get("code") if 0 <= guard.now() - discovered.get("at", 0) < 86400 else None)
        report: dict[str, Any] = {"targetDate": str(args.date), "coverageVerified": False,
                                  "startedAt": datetime.now(SHANGHAI).isoformat(), "results": []}
        output = STATE_DIR / ("report-" + datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S%f") + ".json")
        # 3. 每条完成即保存报告，外部中断后仍有证据；缺代码不猜测，不请求占位 URL。
        for probe in probes:
            if "BSE_CODE" in probe.url and not bse_code:
                record = {"id": probe.id, "source": probe.source, "status": "deferred", "reason": "先探测北交所目录并核对当前代码"}
            else:
                resolved = Probe(probe.id, probe.source, probe.group, probe.url.replace("BSE_CODE", bse_code or ""), probe.shape, probe.board)
                record = fetch(resolved, guard, args.date)
                if probe.id == "em_catalog_BSE" and record["status"] == "sample_received":
                    bse_code = str(record["summary"]["codes"][0])
                    if not re.fullmatch(r"\d{6}", bse_code):
                        bse_code = None
                    else:
                        guard.state["bse_sample"] = {"code": bse_code, "at": guard.now()}
                        guard.save()
            report["results"].append(record)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(f"{record['id']}: {record['status']}", flush=True)
        print(f"报告：{output}")
        return 0 if all(item["status"] == "sample_received" for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
