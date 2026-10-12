from __future__ import annotations

from math import gcd

from app.schemas.video_refinement import RefinementItem


def build_snapshot(source, product, materials) -> dict:
    duration = round(source.duration_seconds * 1000)
    divisor = gcd(source.width, source.height)
    ratio = f"{source.width // divisor}:{source.height // divisor}"
    sample = source.filename == "国光湿巾_成片_001.mp4"
    labels = ["开场商品展示", "使用演示", "商品收尾"] if sample else ["示例开场", "示例主体", "示例收尾"]
    boundaries = [0, round(duration * .23), round(duration * .78), duration]
    timeline = [{"shot_id": f"mock-shot-{index + 1:03d}", "start_ms": boundaries[index],
                 "end_ms": boundaries[index + 1], "label": label, "is_mock": True} for index, label in enumerate(labels)]
    return {
        "schema_version": "video_refinement_snapshot.v1", "is_mock": True,
        "fixture": "guoguang-001.v1" if sample else "generic.v1",
        "source": {"asset_id": source.id, "version": "v1", "filename": source.filename,
                   "object_url": source.object_url, "content_hash": source.content_hash,
                   "product_id": source.product_id, "product_name": product.name,
                   "sku": None, "duration_ms": duration, "width": source.width, "height": source.height,
                   "aspect_ratio": ratio, "fps": source.fps, "has_audio": source.has_audio,
                   "burned_subtitles": source.burned_subtitles, "has_background_music": source.has_background_music},
        "timeline": timeline,
        "transcript": [{"cue_id": "mock-line-001", "start_ms": 0, "end_ms": boundaries[1],
                        "text": f"{product.name}商品展示文案, 待人工核对", "is_mock": True}],
        "subtitles": [{"cue_id": "mock-subtitle-001", "start_ms": 0, "end_ms": boundaries[1],
                       "text": product.name, "is_mock": True}],
        "audio": {"source_kind": "MIXED_OR_UNKNOWN" if source.has_audio else "NONE", "editable_tracks": []},
        "material_catalog": [{"asset_id": row.id, "product_id": row.product_id, "filename": row.filename,
                              "content_hash": row.content_hash, "duration_ms": round(row.duration_seconds * 1000),
                              "object_url": row.object_url, "is_mock_mapping": True} for row in materials],
        "capabilities": {
            "visual": {"state": "PLAN_ONLY", "reason": "可编辑画面替换方案, 尚未接入媒体渲染"},
            "pace": {"state": "PLAN_ONLY", "reason": "可编辑缩短时长和区间, 尚未接入媒体渲染"},
            "voice": {"state": "HUMAN_REQUIRED", "reason": "没有独立配音来源; 混合音轨不能直接替换口播" if source.has_audio else "源片无音轨; 新配音需独立音频来源"},
            "subtitles": {"state": "HUMAN_REQUIRED", "reason": "源片为硬字幕, 缺少干净画面时禁止直接叠加新字幕" if source.burned_subtitles else "没有已验证的独立字幕轨, mock 字幕不能用于渲染"},
            "aspect_ratio_locked": True, "sku_verified": False,
        },
    }


def analyze_mock(snapshot: dict, focus: list[str], brief: str) -> list[RefinementItem]:
    """Build clearly labelled fixture suggestions without claiming media analysis."""
    duration = snapshot["source"]["duration_ms"]
    boundary = snapshot["timeline"][0]["end_ms"]
    if not focus:
        keywords = {"hook": ("开头", "开场", "吸引"), "pace": ("节奏", "加快", "缩短"),
                    "product_visibility": ("画面", "展示", "商品"), "voice": ("口播", "台词", "配音"), "subtitle": ("字幕", "文字")}
        focus = [key for key, words in keywords.items() if any(word in brief for word in words)] or ["hook", "pace", "product_visibility"]
    result = []
    if "hook" in focus or "pace" in focus:
        result.append(RefinementItem(item_id="item-pace", type="PACE_RECUT", range={"start_ms": 0, "end_ms": boundary},
            operation="缩短开场停顿, 更早进入主体", reason="用户希望提高开场或剪辑节奏; 区间采用 mock 时间轴",
            evidence=["mock-shot-001"], confidence_level="MEDIUM", duration_delta_ms=-min(800, boundary // 4),
            risk="需播放源片确认停顿位置, 并检查剪切后原声的连贯性"))
    if "product_visibility" in focus:
        result.append(RefinementItem(item_id="item-visual", type="VISUAL_REPLACE", range={"start_ms": boundary, "end_ms": snapshot["timeline"][1]["end_ms"]},
            operation="用同商品的展示或使用素材替换选定区间", reason="用户希望商品或使用过程更清晰",
            evidence=["mock-shot-002"], confidence_level="LOW", material_asset_id=None,
            risk="候选仅按商品 ID 筛选, SKU 和实际内容尚未核实; 需要人工选择素材",
            dependencies=["同商品素材", "人工核对 SKU"] ))
    for focus_name, item_type, title in [("voice", "VOICE_REPLACE", "核对并修改口播台词"), ("subtitle", "SUBTITLE_EDIT", "核对并修改字幕文本")]:
        if focus_name in focus:
            result.append(RefinementItem(item_id=f"item-{focus_name}", type=item_type, range={"start_ms": 0, "end_ms": duration},
                operation=title, reason="用户要求修改声音或文字, 保存人工方案供后续处理", confidence_level="LOW",
                script="" if focus_name != "voice" else snapshot["transcript"][0]["text"],
                subtitle_text="" if focus_name != "subtitle" else snapshot["subtitles"][0]["text"],
                dependencies=["独立音频或干净源及真实字幕轨"]))
    return result
