# -*- coding: utf-8 -*-
"""
往 data/templates.json 注入「平台」维度与平台专属模板。

可重复执行（幂等）：按 id 覆盖 platforms / families / templates 中同名项，其它数据不动。
用法：~/.workbuddy/binaries/python/envs/default/bin/python tools/patch_platforms.py
"""
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "data", "templates.json")


# ---------------------------------------------------------------- 平台预设
PLATFORMS = [
    {
        "id": "live",
        "name": "直播间封面",
        "kind": "直播",
        "short": "直播",
        "sizes": ["1:1", "3:4", "9:16", "16:9", "2.35:1"],
        "defaultSizes": ["1:1", "9:16"],
        "safeArea": {"top": 0.0, "bottom": 0.0, "note": "直播封面自持画布，无平台 UI 遮挡"},
        "families": ["classic", "youth"],
        "zonesNote": "沿用五分区骨架 + 主播挂件区；有半身像走人物版式，无半身像走无人物版式",
        "note": "视频号 / 抖音直播 / 企微社群 / 公众号全部复用，按尺寸各出一版。",
    },
    {
        "id": "xhs",
        "name": "小红书图文",
        "kind": "图文",
        "short": "小红书",
        "sizes": ["3:4", "1:1"],
        "defaultSizes": ["3:4"],
        "safeArea": {"top": 0.028, "bottom": 0.040, "note": "顶部留作者行，底部留话题标签带；四边再留 6% 出血余量"},
        "families": ["xhs"],
        "zonesNote": "A 作者行 / B 标签条 / C 超大黑体主标题（关键词荧光高亮）/ D 副标题 / E 话题标签带 / F 页码或角标",
        "note": "笔记首图。竖版 3:4 是信息流里占屏最大的比例，标题必须做到「一屏只读三四个字就懂」。",
    },
    {
        "id": "dy_image",
        "name": "抖音图文",
        "kind": "图文",
        "short": "抖音图文",
        "sizes": ["3:4"],
        "defaultSizes": ["3:4"],
        "safeArea": {"top": 0.055, "bottom": 0.085, "note": "顶部避让搜索栏，底部避让文案区与互动条；右下留页码位"},
        "families": ["douyin"],
        "zonesNote": "A 话题条 / B 超大描边主标题（居中）/ C 高亮副标题 / D 角标胶囊 / E 账号行 / F 页码 1/N",
        "note": "图文轮播首图。第 1 张定点击率，后面几张可复用同一模板换序号继续出图。",
    },
    {
        "id": "dy_video",
        "name": "抖音视频封面",
        "kind": "视频",
        "short": "抖音视频",
        "sizes": ["9:16"],
        "defaultSizes": ["9:16"],
        "safeArea": {"top": 0.140, "bottom": 0.220, "note": "顶部 14% 让开状态栏与搜索栏，底部 22% 让开昵称、文案、音乐条与评论框"},
        "families": ["douyin"],
        "zonesNote": "A 话题条 / B 超大描边主标题（居中，落在安全区内）/ D 角标胶囊 / E 账号行 / F 时长角标",
        "note": "全屏首帧封面。抖音会把封面压在右侧信息栏之下，所有关键文字必须落在安全区内。",
    },
]


# ---------------------------------------------------------------- 两个新风格族
FAMILIES = [
    {
        "id": "xhs",
        "name": "小红书笔记",
        "platform": "xhs",
        "note": "浅底大黑字 / 关键词荧光高亮 / 作者行 + 话题标签带，信息流里一眼可读",
    },
    {
        "id": "douyin",
        "name": "抖音竖屏",
        "platform": "douyin",
        "note": "高饱和渐变底 / 超大描边标题 / 话题条 + 页码，按平台安全区内缩排版",
    },
]


# ---------------------------------------------------------------- 平台专属模板
def _pal(**kw):
    return kw


TEMPLATES = [
    # ============================ 小红书图文 ============================
    {
        "id": "tpl_xhs_cream",
        "family": "xhs",
        "platform": "xhs",
        "name": "奶白清单 · 干货笔记型",
        "tagline": "保险科普 / 干货清单 / 认知升级",
        "scenes": ["利率", "宏观", "资产配置", "经济", "通胀", "下行", "趋势", "复利", "增额", "养老", "重疾", "科普"],
        "fontFamily": "sans",
        "align": "left",
        "deco": "xhs-paper",
        "titleStyle": "ink",
        "highlight": True,
        "badgeStyle": "ink-pill",
        "palette": _pal(
            bgTop=[255, 252, 247], bgBottom=[255, 243, 231],
            accent=[255, 93, 79], accent2=[255, 138, 101],
            accentSoft=[255, 93, 79, 30],
            text=[28, 26, 24], subtext=[112, 106, 100], label=[138, 132, 126],
            badgeBg=[28, 26, 24], badgeText=[255, 255, 255], footerText=[158, 152, 146],
            highlight=[255, 219, 105], divider=[232, 224, 214],
            titleGradient=[[28, 26, 24], [28, 26, 24]],
            topicBg=[255, 93, 79, 26],
        ),
    },
    {
        "id": "tpl_xhs_mint",
        "family": "xhs",
        "platform": "xhs",
        "name": "薄荷手账 · 生活种草型",
        "tagline": "家庭保障 / 健康安心 / 亲子规划",
        "scenes": ["家庭", "保障", "健康", "医疗", "孩子", "教育", "亲子", "安心", "储蓄", "工资", "存钱", "钱袋子"],
        "fontFamily": "sans",
        "align": "left",
        "deco": "xhs-paper",
        "titleStyle": "ink",
        "highlight": True,
        "badgeStyle": "ink-pill",
        "palette": _pal(
            bgTop=[247, 253, 250], bgBottom=[226, 245, 238],
            accent=[13, 168, 118], accent2=[46, 196, 152],
            accentSoft=[13, 168, 118, 30],
            text=[19, 36, 31], subtext=[92, 118, 108], label=[120, 142, 134],
            badgeBg=[19, 36, 31], badgeText=[255, 255, 255], footerText=[140, 160, 152],
            highlight=[167, 240, 209], divider=[214, 234, 226],
            titleGradient=[[19, 36, 31], [19, 36, 31]],
            topicBg=[13, 168, 118, 26],
        ),
    },
    # ============================ 抖音图文 ============================
    {
        "id": "tpl_dy_image_ink",
        "family": "douyin",
        "platform": "dy_image",
        "name": "墨黑霓虹 · 硬核科普型",
        "tagline": "利率解读 / 宏观趋势 / 认知颠覆",
        "scenes": ["利率", "宏观", "经济", "通胀", "下行", "政策", "趋势", "复利", "资产配置", "增额"],
        "fontFamily": "sans",
        "align": "center",
        "deco": "dy-aurora",
        "titleStyle": "stroke",
        "badgeStyle": "dy-pill",
        "pageStyle": "image",
        "palette": _pal(
            bgTop=[13, 15, 30], bgBottom=[38, 22, 74],
            accent=[34, 211, 238], accent2=[168, 85, 247],
            accentSoft=[34, 211, 238, 34],
            text=[255, 255, 255], subtext=[196, 208, 232], label=[148, 163, 196],
            badgeBg=[254, 44, 85], badgeText=[255, 255, 255], footerText=[160, 174, 205],
            strokeColor=[6, 8, 20],
            highlight=[250, 204, 21], divider=[52, 40, 92],
            titleGradient=[[255, 255, 255], [206, 226, 255]],
            spark=[[34, 211, 238], [168, 85, 247], [250, 204, 21]],
            topicBg=[34, 211, 238, 40],
        ),
    },
    {
        "id": "tpl_dy_image_lemon",
        "family": "douyin",
        "platform": "dy_image",
        "name": "柠檬高饱和 · 吸睛图文型",
        "tagline": "避坑指南 / 清单盘点 / 强对比",
        "scenes": ["避坑", "踩坑", "清单", "对比", "误区", "真相", "骗局", "买保险", "怎么选"],
        "fontFamily": "sans",
        "align": "center",
        "deco": "dy-rays",
        "titleStyle": "stroke",
        "badgeStyle": "dy-pill",
        "pageStyle": "image",
        "palette": _pal(
            bgTop=[255, 238, 158], bgBottom=[255, 198, 22],
            accent=[255, 45, 85], accent2=[255, 128, 20],
            accentSoft=[255, 45, 85, 34],
            text=[26, 20, 6], subtext=[92, 68, 20], label=[120, 92, 32],
            badgeBg=[255, 45, 85], badgeText=[255, 255, 255], footerText=[128, 100, 36],
            strokeColor=[255, 252, 244],
            highlight=[255, 68, 68], divider=[234, 190, 60],
            titleGradient=[[26, 20, 6], [26, 20, 6]],
            topicBg=[255, 255, 255, 92],
        ),
    },
    # ============================ 抖音视频封面 ============================
    {
        "id": "tpl_dy_video_aurora",
        "family": "douyin",
        "platform": "dy_video",
        "name": "极光渐变 · 视频首帧型",
        "tagline": "趋势解读 / 认知升级 / 高势能",
        "scenes": ["利率", "宏观", "趋势", "资产配置", "经济", "通胀", "下行", "复利", "增额", "政策"],
        "fontFamily": "sans",
        "align": "center",
        "deco": "dy-aurora",
        "titleStyle": "stroke",
        "badgeStyle": "dy-pill",
        "pageStyle": "video",
        "palette": _pal(
            bgTop=[10, 18, 52], bgBottom=[92, 28, 138],
            accent=[56, 189, 248], accent2=[232, 121, 249],
            accentSoft=[56, 189, 248, 34],
            text=[255, 255, 255], subtext=[206, 220, 248], label=[154, 172, 212],
            badgeBg=[254, 44, 85], badgeText=[255, 255, 255], footerText=[168, 184, 220],
            strokeColor=[8, 12, 36],
            highlight=[253, 224, 71], divider=[58, 44, 104],
            titleGradient=[[255, 255, 255], [196, 226, 255]],
            spark=[[56, 189, 248], [232, 121, 249], [253, 224, 71]],
            topicBg=[56, 189, 248, 40],
        ),
    },
    {
        "id": "tpl_dy_video_sunset",
        "family": "douyin",
        "platform": "dy_video",
        "name": "日落橙红 · 视频首帧型",
        "tagline": "家庭保障 / 养老规划 / 温暖向",
        "scenes": ["家庭", "保障", "养老", "退休", "孩子", "教育", "医疗", "健康", "安心", "钱袋子"],
        "fontFamily": "sans",
        "align": "center",
        "deco": "dy-rays",
        "titleStyle": "stroke",
        "badgeStyle": "dy-pill",
        "pageStyle": "video",
        "palette": _pal(
            bgTop=[255, 108, 62], bgBottom=[214, 26, 92],
            accent=[255, 214, 102], accent2=[255, 72, 96],
            accentSoft=[255, 214, 102, 40],
            text=[255, 255, 255], subtext=[255, 232, 220], label=[255, 214, 198],
            badgeBg=[28, 20, 24], badgeText=[255, 255, 255], footerText=[255, 216, 206],
            strokeColor=[128, 18, 46],
            highlight=[255, 233, 130], divider=[255, 168, 140],
            titleGradient=[[255, 255, 255], [255, 236, 210]],
            spark=[[255, 214, 102], [255, 255, 255], [255, 137, 96]],
            topicBg=[255, 255, 255, 64],
        ),
    },
]


# 话题胶囊配色：底色与文字要有足够对比。
# 同色系深浅搭配（红底红字）是最常见的翻车点 —— 缩略图里整条标签会糊成一块色斑。
TOPIC_COLORS = {
    "tpl_xhs_cream":       ([255, 93, 79, 238], [255, 255, 255]),
    "tpl_xhs_mint":        ([13, 168, 118, 238], [255, 255, 255]),
    "tpl_dy_image_ink":    ([34, 211, 238, 232], [8, 14, 30]),
    "tpl_dy_image_lemon":  ([255, 255, 255, 240], [26, 20, 6]),
    "tpl_dy_video_aurora": ([56, 189, 248, 232], [8, 14, 30]),
    "tpl_dy_video_sunset": ([255, 255, 255, 240], [255, 64, 96]),
}


def _apply_topic_colors():
    for t in TEMPLATES:
        c = TOPIC_COLORS.get(t["id"])
        if c:
            t["palette"]["topicBg"] = c[0]
            t["palette"]["topicFg"] = c[1]


def main():
    _apply_topic_colors()
    with io.open(FILE, encoding="utf-8") as f:
        d = json.load(f)

    sp = d["_spec"]
    sp["platforms"] = PLATFORMS
    sp["version"] = "v4.0"
    sp["note"] = (
        "统一模板结构 × 多平台产出。平台（platform）决定画布规格、平台安全区与推荐的视觉风格族；"
        "风格族（family）决定视觉语言；排版模式（layoutMode）由有没有主播半身像决定。三层正交，互不耦合。"
    )

    # families：保留原有两个，追加平台族
    keep = [f for f in sp.get("families", []) if f["id"] not in {x["id"] for x in FAMILIES}]
    sp["families"] = keep + FAMILIES

    # templates：按 id 覆盖
    new_ids = {t["id"] for t in TEMPLATES}
    d["templates"] = [t for t in d["templates"] if t["id"] not in new_ids] + TEMPLATES

    with io.open(FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")

    print("平台:", [p["id"] for p in PLATFORMS])
    print("风格族:", [f["id"] for f in sp["families"]])
    print("模板总数:", len(d["templates"]), "→", [t["id"] for t in d["templates"]])


if __name__ == "__main__":
    main()
