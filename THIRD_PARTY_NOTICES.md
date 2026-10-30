# 第三方组件声明

本项目包含或依赖以下第三方开源组件，其版权归原作者所有。

## pyJianYingDraft

- 位置：`scripts/vendor/pyJianYingDraft/`
- 上游：https://github.com/GuanYixuan/pyJianYingDraft
- 作者：GuanYixuan 及贡献者
- 许可证：MIT License
- 说明：剪映草稿读写与素材/片段/关键帧抽象。本项目对其做了可移植性修补（`pymediainfo` 可选导入、跨平台降级），未改变其许可证义务。

### MIT License

Copyright (c) GuanYixuan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 上游项目

- 上游：https://github.com/luoluoluo22/jianying-editor-skill
- 许可证：MIT License
- 说明：本项目的初始架构与部分工具脚本来自该仓库；v1.0.0 起由米乐维护并二次开发。

## Python 运行时依赖

见 `requirements.txt`。各依赖按其自身许可证分发（MIT / BSD / Apache-2.0 等）。智能分析层的可选开源方案及来源边界如下：

- [PySceneDetect](https://github.com/Breakthrough/PySceneDetect)：BSD-3-Clause；`mile.adapters.detect_scenes` 在已安装 `scenedetect[opencv]` 时优先调用其 `detect(path, ContentDetector())`，未安装时降级 ffmpeg `select=gt(scene,t)` 信号；两者都缺失返回 `SCENE_DETECT_UNAVAILABLE`。只给镜头边界，不理解画面语义。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：随安装包核对仓库 LICENSE；可选 ASR，缺失时返回 `ASR_UNAVAILABLE`。
- [librosa](https://github.com/librosa/librosa)：ISC；可选节拍/音频特征分析，不替代音乐内容理解。
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)：Apache-2.0；可选 OCR 证据层。
- [RapidOCR](https://github.com/RapidAI/RapidOCR)：以仓库 LICENSE 为准；可选轻量 OCR 适配。
- [SAM 2](https://github.com/facebookresearch/sam2)：代码/模型 Apache-2.0，数据集另有许可证；可选主体分割，依赖 PyTorch。
- [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)：Apache-2.0；可选跨软件时间线中间表示，不直接替代剪映草稿 JSON。
- [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm)：以仓库 LICENSE 为准；可选。当前响度实测（`mile.adapters.measure_loudness`）由 ffmpeg `ebur128`（T1）完成，未引入该依赖。

本项目当前默认使用标准库、ffmpeg、numpy 和 vendored `pyJianYingDraft`；上述 T2/T3 能力不会在缺失时伪造结果。
