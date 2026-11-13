"""增量采集业务规则：双轨区间认领、批次回执确认与水位推进都收在这里。

领域约定：
- 水位区间一律按半开区间 [起点, 终点) 认领，终点就是下一段的起点；两份并行认领
  争用相邻边界时不会形成间隙，也不会重叠确认同一段。
- 实时轨链式推进：新认领的起点必须等于当前预留前沿（已确认水位接上在途/已暂停
  认领的终点链）。在途段未确认前，任何作业都不能穿过去推进水位。
- 回扫轨不推进水位：回扫起点由回退步数决定，终点不超过当前已确认水位。
- 批次回执幂等：同一批次号重复确认不会再次前移水位。
- 去重窗口跨双轨生效：窗口内同键样本判重；窗口外的迟到样本仍可补录进结果。
- 水位不可比较时（未明确类型的文字）拒绝认领，绝不退化成字典序比较。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "delta"
REQUIRED_FIELDS = ["游标编号", "目标站点", "增量字段"]
OPTIONAL_FIELDS = ["水位类型", "当前水位", "回退步数", "批次大小", "去重窗口", "重跑开关"]
STATUS_ORDER = ["草稿", "待校验", "运行中", "已暂停"]
WATERMARK_TYPES = ["数值", "时间", "有序文本"]

# 动作 → (允许的前置状态, 目标状态)；暂停/续做会级联处理在途认领
ACTION_RULES = {
    "提交校验": (["草稿"], "待校验"),
    "启动增量": (["待校验"], "运行中"),
    "暂停增量": (["运行中"], "已暂停"),
    "续做增量": (["已暂停"], "运行中"),
}

IN_FLIGHT = ["在途", "已暂停"]
TRACKS = {"realtime": "实时取数", "backfill": "回扫取数"}
MAX_SYNTH_SAMPLES = 60
MAX_KEPT_SAMPLES = 500


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _watermark_key(entry: dict[str, Any], value: Any) -> float | None:
    """把水位映射成可比较的数：数值按大小、时间按天、有序文本按顺序表下标。

    无法比较时返回 None —— 调用方必须拒绝后续比较，不能退化成字典序。
    """
    wm_type = str(entry.get("水位类型") or "").strip()
    text = str(value or "").strip()
    if not text:
        return None
    if wm_type == "数值":
        try:
            return float(text)
        except ValueError:
            return None
    if wm_type == "时间":
        try:
            return datetime.fromisoformat(text).timestamp() / 86400.0
        except ValueError:
            return None
    if wm_type == "有序文本":
        order = entry.get("水位顺序表") or []
        if text in order:
            return float(order.index(text))
        return None
    return None


def _watermark_value(entry: dict[str, Any], key: float) -> str:
    """把比较坐标还原成水位文字，用于回扫起点等由系统算出的水位。"""
    wm_type = str(entry.get("水位类型") or "").strip()
    if wm_type == "数值":
        return str(int(key)) if float(key).is_integer() else str(key)
    if wm_type == "时间":
        return datetime.fromtimestamp(key * 86400.0).isoformat(timespec="seconds")
    if wm_type == "有序文本":
        order = entry.get("水位顺序表") or []
        idx = int(round(key))
        if 0 <= idx < len(order):
            return str(order[idx])
        return ""
    return str(key)


def _ensure_state(entry: dict[str, Any]) -> dict[str, Any]:
    entry.setdefault("claims", [])
    entry.setdefault("segments", [])
    entry.setdefault("samples", [])
    entry.setdefault("applied_receipts", [])
    entry.setdefault("水位顺序表", [])
    return entry


def _reserved_frontier(entry: dict[str, Any]) -> float | None:
    """预留前沿：已确认水位接上在途/已暂停实时认领的终点链。

    暂停的认领仍持有自己的区间（保留认领及候选终点），所以也算进前沿。
    """
    frontier = _watermark_key(entry, entry.get("当前水位"))
    if frontier is None:
        return None
    for claim in entry["claims"]:
        if claim["track"] == "realtime" and claim["status"] in IN_FLIGHT:
            end_key = _watermark_key(entry, claim["end"])
            if end_key is not None:
                frontier = max(frontier, end_key)
    return frontier


class DeltaService:
    # ---------------------------------------------------------------- 列表与登记
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("游标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        if values.get("水位顺序表"):
            entry["水位顺序表"] = list(values["水位顺序表"])
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        _ensure_state(entry)
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"增量游标 {entry_id} 不存在或已归档"
        rule = ACTION_RULES.get(action)
        if rule is None:
            return None, f"动作「{action}」不属于增量采集可执行范围"
        sources, target = rule
        if entry.get("status") not in sources:
            return None, f"游标当前为「{entry.get('status')}」，不能执行{action}"
        _ensure_state(entry)
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = False
        if action == "暂停增量":
            held = [c for c in entry["claims"] if c["status"] == "在途"]
            for claim in held:
                claim["status"] = "已暂停"
            return entry, f"增量游标已暂停：{len(held)} 个在途认领保留认领及候选终点，续做时从原段接着取数"
        if action == "续做增量":
            resumed = [c for c in entry["claims"] if c["status"] == "已暂停"]
            for claim in resumed:
                claim["status"] = "在途"
            return entry, f"增量游标已续做：{len(resumed)} 个认领从暂停段接着取数"
        return entry, f"增量游标已{action}"

    # ---------------------------------------------------------------- 水位类型
    def set_watermark(
        self,
        entry_id: int,
        wm_type: str,
        current: str | None,
        order: list[str],
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """明确水位类型与顺序；返回 (游标, 说明, 是否已可比较)。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"增量游标 {entry_id} 不存在或已归档", False
        wm_type = wm_type.strip()
        if wm_type not in WATERMARK_TYPES:
            return None, f"水位类型须为 {'、'.join(WATERMARK_TYPES)}", False
        order = [str(item).strip() for item in order if str(item).strip()]
        if wm_type == "有序文本" and not order:
            return None, "有序文本水位必须给出显式顺序表，不能凭字典序认领", False
        _ensure_state(entry)
        entry["水位类型"] = wm_type
        if order:
            entry["水位顺序表"] = order
        if current and current.strip():
            entry["当前水位"] = current.strip()
        if _watermark_key(entry, entry.get("当前水位")) is None:
            return entry, (
                f"已记录水位类型为{wm_type}，但当前水位「{entry.get('当前水位')}」"
                "仍无法按该类型比较，请修正当前水位后再认领"
            ), False
        return entry, f"水位类型已明确为{wm_type}，可以开始认领区间", True

    # ---------------------------------------------------------------- 区间认领
    def create_claim(
        self,
        entry_id: int,
        track: str,
        start: str | None,
        end: str | None,
        job: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"增量游标 {entry_id} 不存在或已归档"
        _ensure_state(entry)
        if entry.get("status") != "运行中":
            return None, f"游标当前为「{entry.get('status')}」，请先启动增量再认领区间"
        if track not in TRACKS:
            return None, "轨道须为 realtime（实时取数）或 backfill（回扫取数）"
        current_key = _watermark_key(entry, entry.get("当前水位"))
        if current_key is None:
            return None, (
                f"当前水位「{entry.get('当前水位')}」是不可比较的文字："
                "请先明确水位类型与顺序，不能凭字典序认领区间"
            )
        if track == "realtime":
            return self._claim_realtime(entry, current_key, start, end, job)
        return self._claim_backfill(entry, current_key, end, job)

    def _claim_realtime(
        self,
        entry: dict[str, Any],
        current_key: float,
        start: str | None,
        end: str | None,
        job: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        frontier = _reserved_frontier(entry)
        assert frontier is not None
        start_text = str(start or "").strip() or _watermark_value(entry, frontier)
        start_key = _watermark_key(entry, start_text)
        if start_key is None:
            return None, f"起点水位「{start_text}」无法按「{entry['水位类型']}」解析，请检查认领起点"
        end_text = str(end or "").strip()
        end_key = _watermark_key(entry, end_text)
        if end_key is None:
            return None, f"候选终点「{end_text}」无法按「{entry['水位类型']}」解析，请检查认领终点"
        if end_key <= start_key:
            return None, "候选终点必须大于起点，认领区间不能为空"
        if start_key != frontier:
            return None, (
                f"起点必须从预留前沿 {_watermark_value(entry, frontier)} 开始："
                "在途段未确认前不允许另一作业穿过去推进水位，也不能留下间隙"
            )
        claim = self._append_claim(entry, "realtime", start_text, end_text, job)
        return claim, f"已认领实时区间 [{claim['start']}, {claim['end']})，确认前其他作业不得穿越该段"

    def _claim_backfill(
        self,
        entry: dict[str, Any],
        current_key: float,
        end: str | None,
        job: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        steps = _parse_int(entry.get("回退步数"), 0)
        if steps <= 0:
            return None, "回退步数需为正整数，否则没有可回扫的区间"
        start_key = current_key - steps
        if entry.get("水位类型") == "有序文本":
            start_key = max(0.0, start_key)
        if start_key >= current_key:
            return None, "回扫起点不小于当前水位，请增大回退步数"
        start_text = _watermark_value(entry, start_key)
        end_text = str(end or "").strip() or str(entry.get("当前水位") or "")
        end_key = _watermark_key(entry, end_text)
        if end_key is None:
            return None, f"回扫终点「{end_text}」无法按「{entry['水位类型']}」解析"
        if end_key > current_key:
            return None, "回扫终点不能超过当前已确认水位"
        if end_key <= start_key:
            return None, "回扫终点必须大于回扫起点"
        for claim in entry["claims"]:
            if claim["track"] == "backfill" and claim["status"] in IN_FLIGHT:
                s = _watermark_key(entry, claim["start"])
                e = _watermark_key(entry, claim["end"])
                if s is not None and e is not None and start_key < e and s < end_key:
                    return None, f"回扫区间与在途认领 [{claim['start']}, {claim['end']}) 重叠，请等其确认后再扫"
        claim = self._append_claim(entry, "backfill", start_text, end_text, job)
        return claim, f"已认领回扫区间 [{claim['start']}, {claim['end']})，回扫不推进实时水位"

    def _append_claim(
        self,
        entry: dict[str, Any],
        track: str,
        start: str,
        end: str,
        job: str | None,
    ) -> dict[str, Any]:
        new_id = max((int(c["id"]) for c in entry["claims"]), default=0) + 1
        claim = {
            "id": new_id,
            "track": track,
            "start": start,
            "end": end,
            "status": "在途",
            "job": str(job or "").strip() or f"JOB-{entry.get('游标编号')}-{new_id}",
            "receipts": [],
            "confirmed_end": None,
        }
        entry["claims"].append(claim)
        return claim

    # ---------------------------------------------------------------- 暂停与续做
    def pause_claim(self, entry_id: int, claim_id: int) -> tuple[dict[str, Any] | None, str]:
        entry, claim, message = self._find_claim(entry_id, claim_id)
        if claim is None:
            return None, message
        if claim["status"] != "在途":
            return None, f"认领当前为「{claim['status']}」，只有在途认领可以暂停"
        claim["status"] = "已暂停"
        return entry, f"认领 [{claim['start']}, {claim['end']}) 已暂停：保留认领及候选终点 {claim['end']}，续做时从该段接着取数"

    def resume_claim(self, entry_id: int, claim_id: int) -> tuple[dict[str, Any] | None, str]:
        entry, claim, message = self._find_claim(entry_id, claim_id)
        if claim is None:
            return None, message
        if claim["status"] != "已暂停":
            return None, f"认领当前为「{claim['status']}」，只有已暂停认领可以续做"
        claim["status"] = "在途"
        return entry, f"认领 [{claim['start']}, {claim['end']}) 已续做，从该段接着取数"

    def _find_claim(self, entry_id: int, claim_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, None, f"增量游标 {entry_id} 不存在或已归档"
        _ensure_state(entry)
        for claim in entry["claims"]:
            if int(claim["id"]) == claim_id:
                return entry, claim, ""
        return entry, None, f"认领 {claim_id} 不存在或已确认归档"

    # ---------------------------------------------------------------- 批次回执确认
    def confirm_claim(
        self,
        entry_id: int,
        claim_id: int,
        receipt: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        entry, claim, message = self._find_claim(entry_id, claim_id)
        if claim is None:
            return None, message, {}
        batch = str(receipt.get("批次号") or "").strip()
        if not batch:
            return None, "批次回执缺少批次号", {}
        if batch in entry["applied_receipts"]:
            return entry, f"批次 {batch} 已入账过，重复回执不额外前移水位", {"重复回执": True}
        if claim["status"] == "已确认":
            return None, "该认领已确认，不能重复确认", {}
        if claim["status"] == "已暂停":
            return None, "该认领已暂停，请先续做再提交批次回执", {}
        current_key = _watermark_key(entry, entry.get("当前水位"))
        start_key = _watermark_key(entry, claim["start"])
        end_key = _watermark_key(entry, claim["end"])
        actual_text = str(receipt.get("实际终点") or "").strip() or claim["end"]
        actual_key = _watermark_key(entry, actual_text)
        if current_key is None or start_key is None or end_key is None:
            return None, "水位已不可比较，请先明确水位类型与顺序", {}
        if actual_key is None:
            return None, f"实际终点「{actual_text}」无法按「{entry['水位类型']}」解析", {}
        if not (start_key < actual_key <= end_key):
            return None, f"实际终点须位于认领区间 ({claim['start']}, {claim['end']}] 内", {}
        if claim["track"] == "realtime" and start_key != current_key:
            return None, "前序区间尚未确认，不允许越过在途段推进水位", {}
        stats = self._apply_samples(entry, claim, batch, receipt.get("样本") or [], actual_key, current_key)
        claim["status"] = "已确认"
        claim["confirmed_end"] = actual_text
        claim["receipts"].append(batch)
        entry["applied_receipts"].append(batch)
        kind = "实时确认" if claim["track"] == "realtime" else "回扫覆盖"
        entry["segments"].append({
            "track": claim["track"],
            "kind": kind,
            "start": claim["start"],
            "end": actual_text,
            "job": claim["job"],
            "批次号": batch,
        })
        if claim["track"] == "realtime":
            entry["当前水位"] = actual_text
            move = f"，水位推进至 {actual_text}"
        else:
            move = "，回扫不推进实时水位"
        message = (
            f"批次 {batch} 已确认（{kind} [{claim['start']}, {actual_text})）："
            f"正常 {stats['正常']}、重复 {stats['重复']}、迟到补录 {stats['迟到补录']}{move}"
        )
        return entry, message, stats

    def _apply_samples(
        self,
        entry: dict[str, Any],
        claim: dict[str, Any],
        batch: str,
        raw_samples: list[dict[str, Any]],
        actual_key: float,
        confirmed_before_key: float,
    ) -> dict[str, int]:
        window = float(_parse_int(entry.get("去重窗口"), 0))
        samples = self._normalize_samples(entry, claim, raw_samples, actual_key)
        stats = {"正常": 0, "重复": 0, "迟到补录": 0}
        for sample in samples:
            key = _watermark_key(entry, sample["水位"])
            if key is None:
                continue
            duplicated = any(
                existing["样本键"] == sample["样本键"]
                and existing.get("x") is not None
                and abs(float(existing["x"]) - key) <= window
                for existing in entry["samples"]
            )
            if duplicated:
                verdict = "重复"
            elif key < confirmed_before_key:
                verdict = "迟到补录"
            else:
                verdict = "正常"
            stats[verdict] += 1
            entry["samples"].append({
                "样本键": sample["样本键"],
                "水位": sample["水位"],
                "x": key,
                "track": claim["track"],
                "批次号": batch,
                "判定": verdict,
            })
        if len(entry["samples"]) > MAX_KEPT_SAMPLES:
            del entry["samples"][:-MAX_KEPT_SAMPLES]
        return stats

    def _normalize_samples(
        self,
        entry: dict[str, Any],
        claim: dict[str, Any],
        raw_samples: list[dict[str, Any]],
        actual_key: float,
    ) -> list[dict[str, str]]:
        """回执显式给出的样本优先；未给出时按认领区间等距合成，便于演示双轨去重。"""
        normalized: list[dict[str, str]] = []
        for raw in raw_samples:
            key_text = str(raw.get("样本键") or "").strip()
            mark = str(raw.get("水位") or "").strip()
            if key_text and mark:
                normalized.append({"样本键": key_text, "水位": mark})
            if len(normalized) >= MAX_SYNTH_SAMPLES:
                break
        if normalized:
            return normalized
        start_key = _watermark_key(entry, claim["start"])
        if start_key is None or actual_key <= start_key:
            return []
        span = int(round(actual_key - start_key))
        step = max(1, -(-span // MAX_SYNTH_SAMPLES))
        synthesized = []
        key = start_key
        while key < actual_key:
            mark = _watermark_value(entry, key)
            synthesized.append({"样本键": f"{entry.get('游标编号')}@{mark}", "水位": mark})
            key += step
        return synthesized

    # ---------------------------------------------------------------- 观察窗口
    def get_intervals(self, entry_id: int) -> dict[str, Any] | None:
        """双轨区间总览：已确认段、在途/暂停认领、回扫覆盖与最近样本，全部带坐标。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        _ensure_state(entry)
        current_key = _watermark_key(entry, entry.get("当前水位"))
        comparable = current_key is not None
        reason = None
        if not comparable:
            reason = (
                f"当前水位「{entry.get('当前水位')}」是不可比较的文字："
                "请先明确水位类型与顺序，不能凭字典序认领区间"
            )

        def with_coords(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for item in items:
                x0 = _watermark_key(entry, item.get("start"))
                x1 = _watermark_key(entry, item.get("end"))
                if x0 is None or x1 is None:
                    continue
                out.append({**item, "x0": x0, "x1": x1})
            return out

        live_claims = [c for c in entry["claims"] if c["status"] in IN_FLIGHT]
        realtime_claims = with_coords([c for c in live_claims if c["track"] == "realtime"])
        backfill_claims = with_coords([c for c in live_claims if c["track"] == "backfill"])
        confirmed = with_coords([s for s in entry["segments"] if s["track"] == "realtime"])
        coverage = with_coords([s for s in entry["segments"] if s["track"] == "backfill"])
        frontier = _reserved_frontier(entry) if comparable else None
        samples = [s for s in entry["samples"] if s.get("x") is not None][-200:]

        points = [seg["x0"] for seg in confirmed + realtime_claims + backfill_claims + coverage]
        points += [seg["x1"] for seg in confirmed + realtime_claims + backfill_claims + coverage]
        points += [float(s["x"]) for s in samples]
        base = current_key if comparable else 0.0
        batch = max(_parse_int(entry.get("批次大小"), 100), 1)
        lo = min(points + [base - batch])
        hi = max(points + [base + batch])
        if frontier is not None:
            hi = max(hi, frontier)
        pad = max((hi - lo) * 0.08, batch * 0.2, 1e-6)
        axis = {"min": lo - pad, "max": hi + pad} if comparable else {"min": 0.0, "max": 1.0}

        cursor = {field: entry.get(field) for field in [
            "id", "游标编号", "目标站点", "增量字段", "水位类型", "当前水位",
            "回退步数", "批次大小", "去重窗口", "重跑开关", "水位顺序表", "status",
        ]}
        return {
            "cursor": cursor,
            "可比较": comparable,
            "不可比较原因": reason,
            "预留前沿": _watermark_value(entry, frontier) if frontier is not None else None,
            "前沿坐标": frontier,
            "tracks": {
                "realtime": {"confirmed": confirmed, "claims": realtime_claims},
                "backfill": {"coverage": coverage, "claims": backfill_claims},
            },
            "samples": samples,
            "axis": axis,
        }
