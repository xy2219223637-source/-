"""Frame-accurate video assembly and sample-accurate independent narration."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
import subprocess
import wave

from app.services.director_workflow import _ffmpeg_executable
from app.services.refinement_media import probe, render_recipe, verified_asset, verified_path
from app.services.refinement_timing import PRECISE_TIMING_VERSION, frame_at_ms, frame_rate, precise_tail_allowed, sample_at_frame, technical_tail_allowed


def run(command):
    result = subprocess.run(command,capture_output=True,text=True,errors="replace",timeout=600)
    if result.returncode:
        raise ValueError("精修媒体渲染失败, 请检查素材编码或字幕字体")
    return result


def ass_text(value):
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def ass_time(ms):
    cs = max(0, round(ms/10))
    return f"{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}"


def write_subtitles(plan, path, width, height):
    font = "Microsoft YaHei" if __import__("os").name == "nt" else "Noto Sans CJK SC"
    style = f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,{font},{max(16,round(height*.035))},&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,24,24,{max(20,round(height*.07))},1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    events = []
    for cue in plan["subtitle_track"]:
        if cue["start_ms"] >= cue["end_ms"]:
            raise ValueError("字幕时间为空")
        events.append(f"Dialogue: 0,{ass_time(cue['start_ms'])},{ass_time(cue['end_ms'])},Default,,0,0,0,,{ass_text(cue['text'])}")
    path.write_text(style+"\n".join(events),encoding="utf-8")


def decoded_frame_count(path):
    decoded = run([_ffmpeg_executable(),"-v","error","-xerror","-i",str(path),"-map","0:v:0","-fps_mode","passthrough","-progress","pipe:1","-nostats","-f","null","-"])
    frames = [int(line.partition("=")[2]) for line in decoded.stdout.splitlines() if line.startswith("frame=")]
    return frames[-1] if frames else 0


def render_v3(snapshot, plan, output):
    if plan.get("timing_version") != PRECISE_TIMING_VERSION or plan.get("renderer_version") != "v3-render-1":
        raise ValueError("当前 worker 不支持该精确时间协议版本")
    summary = plan["summary"]
    if (type(summary.get("total_frames")) is not int or summary["total_frames"] <= 0 or summary.get("sample_rate") != 48000
            or summary.get("sample_count") != sample_at_frame(summary["total_frames"], summary["frame_rate"])
            or frame_rate(summary["frame_rate"]) != frame_rate(plan["source"]["frame_rate"])):
        raise ValueError("精确时间方案的总帧数或样本预算无效")
    return render_v2(snapshot, plan, output, precise=True)


def render_v2(snapshot, plan, output, *, precise=False):
    recipe = render_recipe(snapshot,plan)
    source = plan["source"] if precise else snapshot["source"]
    video_track = plan["video_track"]
    narration = plan["narration_track"]
    if not video_track:
        raise ValueError("方案没有可渲染画面")
    catalog = {a["asset_id"]:a for a in [source,*snapshot["material_catalog"]]}
    assets = {v["id"]:v for v in plan.get("voiceover_assets",[])}
    paths = {asset_id:verified_path(verified_asset(catalog[asset_id],plan)) for asset_id in {v["source_asset_id"] for v in video_track} | {source["asset_id"]}}
    voice_paths = {key:verified_path(asset) for key,asset in assets.items()}
    metadata = {key:probe(p) for key,p in paths.items()}
    width,height,fps = source["width"],source["height"],source["fps"]
    if precise:
        fps = str(frame_rate(plan["summary"]["frame_rate"]))
    numeric_fps = float(frame_rate(fps))
    if width%2 or height%2 or (metadata[source["asset_id"]]["width"],metadata[source["asset_id"]]["height"]) != (width,height):
        raise ValueError("源片实测画幅与方案不一致或无法编码")
    expected_ms = plan["summary"]["expected_duration_ms"]
    total_frames = plan["summary"]["total_frames"] if precise else frame_at_ms(expected_ms, fps)
    expected_seconds = float(Fraction(total_frames, 1)/frame_rate(fps)) if precise else expected_ms/1000
    parts = []
    cursor = 0
    for index,clip in enumerate(video_track):
        a,b = clip["source_range"]["start_ms"],clip["source_range"]["end_ms"]
        left,right = clip["output_range"]["start_ms"],clip["output_range"]["end_ms"]
        if precise:
            left,right = clip["output_frames"]["start_frame"],clip["output_frames"]["end_frame"]
        if left != cursor or right <= left or (not precise and b-a != right-left):
            raise ValueError("画面轨没有连续完整覆盖输出")
        asset = clip["source_asset_id"]
        if b > round(metadata[asset]["duration_seconds"]*1000)+(10 if precise else round(1000/numeric_fps)):
            raise ValueError("素材实测长度不足")
        part = output.parent / f"video-{index:04d}.mp4"
        frames = right-left if precise else frame_at_ms(right, fps) - frame_at_ms(left, fps)
        if frames <= 0:
            raise ValueError("画面片段不足一帧")
        # Let fps consume timestamp lookahead before trimming. Feeding fps an
        # artificial EOF at every cut can discard its final valid output frame.
        duration = (b-a)/1000 + (.0005 if precise else 0)
        input_args = ["-ss",str(a/1000),"-i",str(paths[asset])]
        temporal = f"setpts=PTS-STARTPTS,fps={fps},trim=duration={duration}"
        if precise and clip["source_kind"] == "material":
            # Reject frames outside the selected source timestamps before resetting
            # PTS. A seek followed by setpts alone can hide a late first frame.
            temporal = f"trim=start=0:end={duration},setpts=PTS-STARTPTS,fps={fps},trim=end_frame={frames}"
        if precise and clip["source_kind"] == "source":
            bounds = clip["source_normalized_frames"]
            input_args = ["-i",str(paths[asset])]
            temporal = f"setpts=PTS-STARTPTS,fps={fps},trim=start_frame={bounds['start_frame']}:end_frame={bounds['end_frame']},setpts=PTS-STARTPTS"
        run([_ffmpeg_executable(),"-y",*input_args,"-vf",f"{temporal},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p","-an","-frames:v",str(frames),"-r",str(fps),"-c:v","libx264","-preset","veryfast","-crf","20","-threads","2","-video_track_timescale","120000","-map_metadata","-1",str(part)])
        try:
            measured_frames = decoded_frame_count(part) if precise else frames
        except ValueError:
            measured_frames = 0
        if measured_frames != frames:
            raise ValueError(f"源区间不能解码出所需画面帧, 禁止复制末帧或越界补足: {index}, {measured_frames}/{frames}")
        parts.append(part); cursor = right
    if cursor != (total_frames if precise else expected_ms):
        raise ValueError("画面时长与方案不一致")
    concat = output.parent/"video-concat.txt"
    concat.write_text("\n".join(f"file '{p.name}'" for p in parts),encoding="utf8")
    joined = output.parent/"joined.mp4"
    run([_ffmpeg_executable(),"-y","-f","concat","-safe","1","-display_rotation","0","-i",str(concat),"-c","copy","-map_metadata","-1",str(joined)])
    audio_file = output.parent/"narration.wav"
    rate,channels = 48000,2
    audio_cursor = 0
    with wave.open(str(audio_file),"wb") as stream:
        stream.setnchannels(channels);stream.setsampwidth(2);stream.setframerate(rate)
        for index,clip in enumerate(narration):
            left,right = clip["output_range"]["start_ms"],clip["output_range"]["end_ms"]
            start_sample,end_sample = round(left*rate/1000),round(right*rate/1000)
            if precise:
                start_sample,end_sample = clip["output_samples"]["start_sample"],clip["output_samples"]["end_sample"]
                if (start_sample,end_sample) != tuple(sample_at_frame(clip["output_frames"][key], fps) for key in ("start_frame","end_frame")):
                    raise ValueError("音频轨样本边界与确认帧边界不一致")
            if start_sample < audio_cursor:
                raise ValueError("叙事音频轨存在重叠")
            if start_sample > audio_cursor:
                # Gaps are only possible when no original audio exists or an explicit no-speech insertion was accepted.
                stream.writeframes(b"\0"*((start_sample-audio_cursor)*channels*2))
            a,b = clip["source_range"]["start_ms"],clip["source_range"]["end_ms"]
            tts = clip["source_kind"] == "tts"
            path = voice_paths[clip["source_asset_id"]] if tts else paths[source["asset_id"]]
            if not precise and tts and b-a > right-left:
                raise ValueError("配音长于输出区间, 禁止截断语音")
            part = output.parent/f"audio-{index}.pcm"
            clip_samples = round((b-a)*rate/1000)
            # A rounded duration is not permission to drop a decoded TTS tail.
            # Trim original mixed audio before loudnorm, never with an output -t.
            filters = "aresample=48000," + ("" if tts else f"atrim=end_sample={clip_samples},") + "asetpts=N/SR/TB"
            audio_input = ["-ss",str(a/1000),"-i",str(path)]
            if precise and not tts:
                bounds = clip["source_samples"]
                audio_input = ["-i",str(path)]
                filters = f"aresample=48000,atrim=start_sample={bounds['start_sample']}:end_sample={bounds['end_sample']},asetpts=N/SR/TB"
            if tts:
                # Compare real adjacent audio loudness; processing is confined to newly generated narration.
                target = -16.0
                previous = next((r for r in plan["source_to_output_map"] if r["kind"]=="retained" and r["output_range"]["end_ms"] <= left),None)
                if previous and source["has_audio"]:
                    endpoint = previous["source_range"]["end_ms"]/1000
                    scan = run([_ffmpeg_executable(),"-hide_banner","-ss",str(max(0,endpoint-3)),"-i",str(paths[source["asset_id"]]),"-t","3","-af","loudnorm=print_format=json","-vn","-f","null","-"])
                    try:
                        values = json.loads(scan.stderr[scan.stderr.rfind("{"):])
                        target = max(-24,min(-14,float(values["input_i"])))
                    except (ValueError,KeyError):
                        pass
                filters += f",loudnorm=I={target}:TP=-1.5:LRA=11,aresample=48000"
            run([_ffmpeg_executable(),"-y",*audio_input,"-vn","-af",filters,"-ar",str(rate),"-ac",str(channels),"-f","s16le",str(part)])
            pcm = part.read_bytes()
            samples = len(pcm)//(channels*2)
            if precise and tts and samples != clip["source_samples"]["end_sample"]:
                raise ValueError("配音实测样本数与绑定资产不一致")
            needed = end_sample-start_sample
            if samples > needed:
                if tts or precise:
                    raise ValueError("配音样本长于区间, 请扩大画面选段" if tts else "源音轨样本长于确认区间, 禁止截断原声")
                pcm = pcm[:needed*channels*2]
            elif samples < needed:
                missing = needed-samples
                display_ms = clip.get("padding_policy", {}).get("max_display_ms", 0)
                bounded_display = 0 < display_ms <= 100 and missing*1000 <= display_ms*rate
                allowed = (precise_tail_allowed(missing, fps, display_ms=display_ms) if precise else
                           technical_tail_allowed(missing, rate, fps) or bounded_display or
                           "padding_policy" not in clip and bool(clip.get("padding_reason")))
                if not allowed:
                    raise ValueError("音频实测长度不足, 禁止静音填充缺失台词")
                pcm += b"\0"*(missing*channels*2)
            stream.writeframes(pcm)
            audio_cursor = end_sample
        end = sample_at_frame(total_frames, fps) if precise else round(expected_ms*rate/1000)
        if audio_cursor < end:
            stream.writeframes(b"\0"*((end-audio_cursor)*channels*2))
    command = [_ffmpeg_executable(),"-y","-i",str(joined)]
    if narration:
        command += ["-i",str(audio_file)]
    if plan["subtitle_track"]:
        subtitle_file = output.parent/"refinement.ass"
        write_subtitles(plan,subtitle_file,width,height)
        escaped = str(subtitle_file).replace("\\","/").replace(":","\\:").replace("'","\\'")
        command += ["-vf",f"ass=filename='{escaped}'","-c:v","libx264","-preset","veryfast","-crf","20"]
    else:
        command += ["-c:v","copy"]
    command += ["-map","0:v:0"]
    command += ["-map","1:a:0","-c:a","aac","-ar","48000","-ac","2"] if narration else ["-an"]
    command += ["-t",str(expected_seconds),"-map_metadata","-1","-movflags","+faststart",str(output)]
    run(command)
    measured = probe(output)
    frame_tolerance = 1/numeric_fps + 1024/rate
    if abs(measured["duration_seconds"]-expected_seconds) > frame_tolerance or (measured["width"],measured["height"]) != (width,height) or bool(measured["has_audio"]) != bool(narration):
        raise ValueError("完整预览实测时长或音画规格不符合确认方案")
    frames = decoded_frame_count(output)
    if frames != total_frames:
        raise ValueError(f"完整预览实测帧数与确认方案不一致: {frames} / {total_frames}")
    measured.update(passed=True,decode_checked=True,expected_duration_seconds=expected_seconds,
                    decoded_frames=frames,
                    burned_subtitles=bool(plan["subtitle_track"]) or any(catalog[r["source_asset_id"]].get("burned_subtitles",False) for r in video_track),
                    has_background_music=any(r["source_kind"]=="source" for r in narration) and source.get("has_background_music",False))
    if precise:
        with wave.open(str(audio_file), "rb") as stream:
            measured.update(pcm_sample_count=stream.getnframes(),sample_rate=stream.getframerate(),frame_rate=fps,
                            timing_version=PRECISE_TIMING_VERSION,container_delta_seconds=measured["duration_seconds"]-expected_seconds)
            if stream.getnframes() != sample_at_frame(total_frames, fps):
                raise ValueError("输出 PCM 样本总数与全局帧预算不一致")
    return measured,recipe
