import os
import json
import shutil
import uuid
import pyJianYingDraft as draft

class MockVideoMaterial(draft.VideoMaterial):
    def __init__(self, material_id, duration, name, path):
        self.material_id = material_id
        self.duration = duration
        self.material_name = name
        self.path = path
        self.width = 1920
        self.height = 1080
        self.material_type = "video"
    def serialize(self):
        return {
            "id": self.material_id, "type": "video", "name": self.material_name, "path": self.path,
            "duration": self.duration, "material_id": self.material_id
        }

class MockAudioMaterial(draft.AudioMaterial):
    def __init__(self, material_id, duration, name, path):
        self.material_id = material_id
        self.duration = duration
        self.material_name = name
        self.path = path
    def serialize(self):
        return {
            "id": self.material_id, "type": "audio", "name": self.material_name, "path": self.path,
            "duration": self.duration, "material_id": self.material_id
        }

class CompoundSegment:
    def __init__(self, material_id, target_timerange):
        self.material_id = material_id
        self.target_timerange = target_timerange
    def serialize(self):
        return {
             "id": str(uuid.uuid4()).upper(), "material_id": self.material_id,
             "target_timerange": self.target_timerange.serialize(),
             "render_index": 0, "type": "video"
        }

class MockingOpsMixin:
    """
    JyProject 的协议补丁与伪物料 Mixin。
    """
    def _force_activate_adjustments(self):
        content_path = os.path.join(self.root, self.name, "draft_content.json")
        if not os.path.exists(content_path): return

        try:
            with open(content_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            has_modified = False
            materials = data.setdefault("materials", {})
            all_effects = materials.setdefault("effects", [])

            PROP_MAP = {"KFTypeBrightness": "brightness", "KFTypeContrast": "contrast", "KFTypeSaturation": "saturation"}
            jy_res_path = "C:/Program Files/JianyingPro/5.9.0.11632/Resources/DefaultAdjustBundle/combine_adjust"

            for track in data.get("tracks", []):
                for seg in track.get("segments", []):
                    kfs = seg.get("common_keyframes", [])
                    active_props = [kf.get("property_type") for kf in kfs if kf.get("property_type") in PROP_MAP]

                    if active_props:
                        seg["enable_adjust"] = True
                        seg["enable_color_correct_adjust"] = True
                        refs = seg.setdefault("extra_material_refs", [])

                        for prop in active_props:
                            mat_type = PROP_MAP[prop]
                            if not any(m.get("type") == mat_type and m["id"] in refs for m in all_effects):
                                new_id = str(uuid.uuid4()).upper()
                                shadow_mat = {
                                    "type": mat_type, "value": 0.0, "path": jy_res_path, "id": new_id,
                                    "apply_target_type": 0, "platform": "all", "source_platform": 0, "version": "v2"
                                }
                                all_effects.append(shadow_mat)
                                refs.append(new_id)
                                has_modified = True

            if has_modified:
                with open(content_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Force activation failed: {e}")

    def _patch_styled_text_paths(self):
        """把本地花字资源复制进草稿并回填 effectStyle.path。"""
        styles = getattr(self, "_styled_text_styles", None)
        if not styles:
            return
        content_path = os.path.join(self.root, self.name, "draft_content.json")
        if not os.path.exists(content_path):
            return

        resource_root = os.path.join(self.root, self.name, "Resources", "artistEffect")
        resolved = {}
        for style_id, src_dir in styles.items():
            dst_dir = os.path.join(resource_root, style_id)
            try:
                if os.path.isdir(src_dir) and not os.path.isdir(dst_dir):
                    shutil.copytree(src_dir, dst_dir)
                resolved[style_id] = dst_dir.replace("\\", "/")
            except Exception as exc:
                print(f"[warn] 花字资源复制失败 {style_id}: {exc}")

        try:
            with open(content_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            has_modified = False
            for material in (data.get("materials") or {}).get("texts") or []:
                raw = material.get("content")
                if not raw:
                    continue
                try:
                    content = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    continue
                for style in content.get("styles") or []:
                    effect = style.get("effectStyle") or {}
                    style_id = str(effect.get("id") or "")
                    if style_id in resolved:
                        effect["path"] = resolved[style_id]
                        style["effectStyle"] = effect
                        has_modified = True
                material["content"] = json.dumps(content, ensure_ascii=False)

            if has_modified:
                with open(content_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
        except Exception as exc:
            print(f"⚠️ 花字路径回填失败: {exc}")

    def _patch_cloud_material_ids(self):
        if not self._cloud_audio_patches and not self._cloud_text_patches: return
        content_path = os.path.join(self.root, self.name, "draft_content.json")
        if not os.path.exists(content_path): return

        try:
            with open(content_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            has_modified = False
            materials = data.get("materials", {})
            audios = materials.get("audios", [])
            for mat in audios:
                path = mat.get("path", "")
                for dummy_path, patch_info in self._cloud_audio_patches.items():
                    if dummy_path in path:
                        if patch_info["type"] == "music":
                            mat["music_id"] = patch_info["id"]
                            mat["type"] = "music"
                            has_modified = True
            
            if has_modified:
                with open(content_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass
