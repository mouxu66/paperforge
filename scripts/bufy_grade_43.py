"""Bufy 43 篇感悟报告人工评分"""
from __future__ import annotations
import json
import csv
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROWS = [
    ("reflection_000_999900000001-学生01.docx", "999900000001", "学生01", "Remote Laboratories: E-Lab (INSA Lyon 2007)", 4, 4318, 0.85, 0.70, 0.65, 0.85, 0.85, 0.70, "OPC/OROCOS/IMS-LD 标准讲清。reflection 5 条: 标准化、虚实结合、跨域延伸、IMS-LD Level A 批评、对当下云/IoT/5G 呼应 — 慢热但深"),
    ("reflection_001_999900000002-学生02（1）.docx", "999900000002", "学生02", "Empathic Robot for Group Learning", 4, 2394, 0.75, 0.55, 0.30, 0.55, 0.85, 0.55, "63 人 3 组 / p=0.01 深度对话。但 d 段仅 8 字 — 反思丢失"),
    ("reflection_002_999900000003-学生04-实验报告.docx", "999900000003", "学生04", "Chronos/CRS 微型类车机器人", 2, 2091, 0.75, 0.50, 0.45, 0.65, 0.85, 0.45, "q 短但 tech 1437 字塞下全部。6 组控制器对比。reflection 只一句「够用就好」"),
    ("reflection_003_999900000004-学生05（1）.docx", "999900000004", "学生05", "Toward Family-Robot Interactions (HRI 2024)", 4, 7138, 0.90, 0.85, 0.80, 0.90, 0.90, 0.85, "班里最高质量。12 篇综述 + 6 项实验 + 家庭系统理论框架 + 6 条参考文献。reflection 2406 字多维分析"),
    ("reflection_004_999900000005-学生03（1）.docx", "999900000005", "学生03", "多智能体四旋翼无人机 RL 仿真", 4, 2882, 0.85, 0.55, 0.40, 0.70, 0.90, 0.55, "数据扎实（1.5m/2.5m/s/100Hz），但创新不足"),
    ("reflection_005_999900000006-学生06.docx", "999900000006", "学生06", "Crazyflie/RotorPy 仿真器教学", 3, 3663, 0.85, 0.65, 0.20, 0.70, 0.80, 0.85, "RK45/Dryden 风场/MOCAP/几何控制器。但**缺独立反思** — 复制粘贴特征"),
    ("reflection_006_999900000007-学生07（1）.docx", "999900000007", "学生07", "IDS3C 1:25 缩尺 智能城市 CAVs", 3, 1074, 0.70, 0.45, 0.55, 0.60, 0.85, 0.65, "偏短。Unity+AirSim+VICON+50 辆 4WS。reflection 浅浅一层"),
    ("reflection_007_999900000008-学生08（1）.docx", "999900000008", "学生08", "dVRK + RL + AR 手术智能教学", 4, 1157, 0.85, 0.60, 0.55, 0.85, 0.85, 0.65, "数据密度高 (92.6%/80.2%/<1像素/161ms/1ms)。4 段紧扣 a/b/c/d。小篇幅高质量"),
    ("reflection_008_999900000009-学生09.docx", "999900000009", "学生09", "MICROMVP 低成本多车协同平台", 4, 3974, 0.85, 0.70, 0.75, 0.80, 0.90, 0.85, "reflection 1649 字班里少见。三层: 专业学习/教学理念/未来职业(师范→信息技术教师)"),
    ("reflection_009_999900000010-学生10（1）.docx", "999900000010", "学生10", "SMARTmBOT 基于 ROS2 低成本机器人", 2, 4363, 0.85, 0.65, 0.40, 0.75, 0.85, 0.60, "tech 3338 字极详细 ROS2 节点。**缺独立 reflection 段** — 塞进 tech 段尾"),
    ("reflection_010_999900000011-学生11.docx", "999900000011", "学生11", "HERON (欧盟 H2020) UGV+UAV 道路养护", 4, 1953, 0.85, 0.65, 0.45, 0.85, 0.90, 0.80, "HERON 听懂率高。表 1/2 忠实。reflection 没超论文结论太远"),
    ("reflection_011_999900000012-学生12（1）.docx", "999900000012", "学生12", "儿童-社交机器人综述 NAO/ASIMO/Pepper", 2, 1451, 0.65, 0.45, 0.40, 0.55, 0.80, 0.70, "态度差。模式化 (结果:…；表现:…)。缺灵魂"),
    ("reflection_012_999900000013-学生13.docx", "999900000013", "学生13", "ChatGPT 解 Parsons 编程题(多模态)", 2, 1942, 0.80, 0.55, 0.55, 0.70, 0.85, 0.60, "Parsons + 多模态 AI 风险。reflection 477 字精炼。但只 2 段缺 exp"),
    ("reflection_013_999900000014-学生14.docx", "999900000014", "学生14", "PAiREd Misty II + GPT-4o 三方", 3, 2214, 0.85, 0.65, 0.45, 0.85, 0.85, 0.70, "PAiREd 三层架构清楚。RQ4 矩阵简洁。**reflection 在 exp 段尾** — parser 未识别"),
    ("reflection_014_999900000015-学生15（1）.docx", "999900000015", "学生15", "SwarmLab MATLAB 集群仿真", 4, 2542, 0.80, 0.55, 0.50, 0.80, 0.90, 0.70, "Olfati-Saber vs Vasarhelyi 对比。**reflection 段只 71 字标题+占位**"),
    ("reflection_015_999900000016-学生16（1）.docx", "999900000016", "学生16", "ARtonomous iPad + RL 中学生教学", 3, 1984, 0.85, 0.65, 0.55, 0.85, 0.85, 0.65, "86.7%/5.99/100%。reflection 有教育哲学洞察 (脚手架/试错场景)"),
    ("reflection_016_999900000017-学生17（2）.docx", "999900000017", "学生17", "Parent-child-robot Math Talk (Misty)", 3, 1663, 0.60, 0.35, 0.30, 0.40, 0.80, 0.40, "态度差。q 是反思段, exp 6 空字 — 模板未替换。草稿"),
    ("reflection_017_999900000018-学生18-实验报告.docx", "999900000018", "学生18", "The Inverted Pendulum 倒立摆 (arXiv 1405.3094)", 2, 2693, 0.90, 0.80, 0.75, 0.85, 0.85, 0.80, "q 段 1860 字综述+历史 (PID/LQR/滑模/MPC)。reflection 653 字谈「简单即深刻」、果蝇类比、跨域。只有 q+reflection 两段"),
    ("reflection_018_999900000019-学生19.docx", "999900000019", "学生19", "PhysicsAssistant 物理实验大模型机器人", 4, 1715, 0.85, 0.60, 0.55, 0.80, 0.85, 0.65, "布鲁姆分类 + GPT-3.5 vs GPT-4 (3.8/3.0/3.2)。reflection 中规中矩"),
    ("reflection_019_999900000021-学生20.docx", "999900000021", "学生20", "Surgical-VQLA++ 对抗对比鲁棒", 4, 1609, 0.85, 0.65, 0.50, 0.85, 0.85, 0.65, "0.6568 Acc / 0.7982 mIoU / +1.64% 超第二名。reflection 334 字少批判"),
    ("reflection_020_999900000022-学生21（1）.docx", "999900000022", "学生21", "生成式 AI 计算机教育综述", 4, 2253, 0.85, 0.70, 0.65, 0.85, 0.85, 0.75, "综述类 (71 篇 + 171 学 + 57 师)。数据精 (80% 2023 前 8 月 / CS1 90%)"),
    ("reflection_021_999900000023-学生22-实验报告.docx", "999900000023", "学生22", "DQ Robotics 对偶四元数开源建模", 2, 2097, 0.80, 0.55, 0.50, 0.70, 0.85, 0.55, "只 exp+tech 两段。反射段提跨语言、模块化优势"),
    ("reflection_022_999900000024-学生23.docx", "999900000024", "学生23", "Misty II 亲子学习 三方协作", 3, 3978, 0.85, 0.65, 0.50, 0.85, 0.90, 0.75, "q 408 字细 + tech 1382 字 (P1-P9 家长原话全程引用)。**引用密度极高**"),
    ("reflection_023_999900000025-学生24.docx", "999900000025", "学生24", "Social Assistive Robots SAR 全球部署综述", 3, 5929, 0.85, 0.70, 0.50, 0.85, 0.85, 0.80, "最长 5929 字。综述听懂率高。缺 tech 段(机器人软硬件)"),
    ("reflection_024_999900000026-学生25（1）.docx", "999900000026", "学生25", "A2Nav Action-Aware Navigation 导航", 3, 2806, 0.85, 0.55, 0.50, 0.75, 0.85, 0.55, "经典导航方法 + 实验结果。但 reflection 短、缺跨领域"),
    ("reflection_025_999900000027-学生26（1）.docx", "999900000027", "学生26", "HapticBots 分布式变形机器人 VR 触觉", 3, 2604, 0.85, 0.65, 0.55, 0.85, 0.85, 0.65, "数据: 倾斜 0/15/30/50/70°、连续感 6.8 分 vs 4.1 分对比。cross-discipline"),
    ("reflection_026_999900000028-学生27-实验报告.docx", "999900000028", "学生27", "CS-VQLA 医学手术视觉问答持续学习", 4, 2797, 0.85, 0.65, 0.40, 0.85, 0.85, 0.60, "学习总结: 灾难性遗忘/数据隐私/类别干扰。**reflection 段被切 78 字占位**"),
    ("reflection_027_999900000029-学生28-实验报告.docx", "999900000029", "学生28", "Cambridge Minicar 阿克曼微型车队", 4, 2472, 0.85, 0.70, 0.65, 0.85, 0.85, 0.80, "Cambridge Minicar 76.5 美元 / 16 车 U 形。reflection 646 字 4 维度"),
    ("reflection_028_999900000030-学生29.docx", "999900000030", "学生29", "HeRo 2.0 低成本集群机器人", 2, 1872, 0.85, 0.60, 0.40, 0.70, 0.85, 0.60, "只 q+tech 两段。**实验结果塞在 q** — 不合规"),
    ("reflection_029_999900000032-学生30（1）.docx", "999900000032", "学生30", "MRS UAV System 多旋翼无人机控制", 3, 3243, 0.90, 0.75, 0.70, 0.90, 0.90, 0.85, "听懂率极高。MBZIRC 2017/2020、DARPA 地下赛。reflection 4 条有见解"),
    ("reflection_030_999900000033-学生31.docx", "999900000033", "学生31", "AWS DeepRacer 强化学习自动驾驶", 3, 1773, 0.80, 0.55, 0.50, 0.75, 0.80, 0.65, "ROS+Gazebo+RL Coach+SageMaker+OpenVINO。sim2real 鲁棒性"),
    ("reflection_031_999900000034-学生32.docx", "999900000034", "学生32", "工业机器人多目标贝叶斯优化先验融合", 4, 1545, 0.85, 0.70, 0.60, 0.85, 0.85, 0.75, "HV 提升 40% / 迭代减 60%。reflection 谈「纯算法导向 → 问题导向」"),
    ("reflection_032_999900000035-学生33（1）.docx", "999900000035", "学生33", "儿童与社交机器人开箱体验 (CHI '22)", 3, 3407, 0.85, 0.65, 0.55, 0.80, 0.85, 0.75, "CHI '22 听懂。RQ1/RQ2 + 开箱 5 大要素。**缺独立 reflection**"),
    ("reflection_033_999900000036-学生34（1）.docx", "999900000036", "学生34", "iRoPro 终端用户机器人交互编程", 4, 2784, 0.90, 0.75, 0.70, 0.85, 0.85, 0.80, "3 个基础动作完成 6 项任务 / 41.2 分钟 / 70% 用户认可。reflection 694 字 4 维"),
    ("reflection_034_999900000037-学生35（1）.docx", "999900000037", "学生35", "SPRITE 自闭症机器人 8 周家庭干预", 3, 1157, 0.85, 0.65, 0.50, 0.85, 0.85, 0.65, "hHRL + Q-learning 个性化。65% 参与度。reflection 嵌入 exp 段尾"),
    ("reflection_035_999900000038-学生36.docx", "999900000038", "学生36", "Stanford Pupper 低成本四足机器人", 4, 2345, 0.90, 0.75, 0.65, 0.85, 0.90, 0.80, "Pupper 2.1kg/12DOF/$2000/8h。3 院校搭建 0.66±0.025m/s"),
    ("reflection_036_999900000039-学生37（1）.docx", "999900000039", "学生37", "BREMEN 算法 模型离线优化部署效率", 4, 1272, 0.85, 0.60, 0.55, 0.80, 0.85, 0.60, "5-10 次部署学成。数据利用率 10-20 倍。reflection 谈 K12 教育延伸"),
    ("reflection_037_999900000040-学生38（1）.docx", "999900000040", "学生38", "SUPR-GAN 手术阶段预测 GAN", 4, 2074, 0.85, 0.60, 0.55, 0.85, 0.85, 0.60, "GAN 序列预测 + Gumbel-Softmax + 16 外科医生盲测 53.5%。但**tech 段仅 41 字**"),
    ("reflection_038_999900000041-学生39（1）.docx", "999900000041", "学生39", "MuSHR 低成本开源自主赛车", 4, 2045, 0.85, 0.70, 0.65, 0.85, 0.90, 0.75, "MuSHR $600 vs MIT RACECAR $1000。reflection 537 字 4 维"),
    ("reflection_039_999900000042-学生40（1）.docx", "999900000042", "学生40", "iRoPro 终端用户机器人编程框架", 4, 1754, 0.85, 0.55, 0.45, 0.65, 0.85, 0.55, "与学生34同 paper。q 简短有 PDDL/Rapid PbD。reflection 197 字**短且泛**"),
    ("reflection_040_999900000043-学生41（1）.docx", "999900000043", "学生41", "panda-py: Franka 机器人 Python 接口", 4, 3508, 0.90, 0.70, 0.65, 0.85, 0.85, 0.85, "panda-py vs libfranka(C++) vs ROS"),
    ("reflection_041_实验报告.docx", "unknown", "(匿名)", "(与 #42 类似 CS-VQLA)", 4, 2797, 0.80, 0.55, 0.40, 0.80, 0.80, 0.55, "无学号。reflection 占位短"),
    ("reflection_042_机器人技术及其在中小学教育中的应用-999900000020.docx", "999900000020", "冯宇丽", "iRoPro 终端用户机器人编程 (or VQLA 综述)", 3, 2822, 0.80, 0.65, 0.55, 0.75, 0.80, 0.65, "Baxter + 41.2 分钟 + 70%。reflection 短但谈「师范生技术服务教学」"),
]


def compute(r):
    file, sid, name, paper, secs, chars, ua, ad, ii, es, fid, cov, note = r
    avg = round(0.05 * ua + 0.15 * ad + 0.35 * ii + 0.05 * es + 0.05 * fid + 0.35 * cov, 4)
    # verdict
    if avg >= 0.66 and ii >= 0.55 and ua >= 0.70 and es >= 0.65:
        verdict = "well_done"
    elif avg < 0.55:
        verdict = "rewrite_required" if (ii < 0.40 or es < 0.45) else "needs_evidence"
    elif ii < 0.45:
        verdict = "needs_depth"
    elif es < 0.60:
        verdict = "needs_evidence"
    else:
        verdict = "needs_depth" if ii < 0.55 else "well_done"
    flags = []
    if secs < 4:
        flags.append("missing-sections=" + str(4 - secs))
    if chars < 1200:
        flags.append("short")
    if ii < 0.50:
        flags.append("ii-weak")
    if es < 0.60:
        flags.append("es-weak")
    if "\u53cd\u601d\u7a7a\u6d1e" in note or "\u7f3a\u72ec\u7acb\u53cd\u601d" in note:
        flags.append("reflection-stuffed")
    if "\u6001\u5ea6\u5dee" in note or "\u8349\u7a3f" in note:
        flags.append("attitude-bad")
    return {
        "id": None,  # filled later
        "file": file, "sid": sid, "name": name, "paper": paper,
        "section_keys_count": secs, "chars": chars,
        "ua": ua, "ad": ad, "ii": ii, "es": es, "fid": fid, "cov": cov,
        "bufy_average": avg, "bufy_verdict": verdict,
        "bufy_flags": ",".join(flags) if flags else "ok",
        "bufy_note": note,
    }


def main():
    rows = [compute(r) for r in ROWS]
    for i, r in enumerate(rows, 1):
        r["id"] = i

    print("\n=== Bufy 43 篇 总览（按平均分倒序）===")
    header = ["sid", "name", "sec", "chars", "ua", "ad", "ii", "es", "fid", "cov", "avg", "verdict"]
    print("\t".join(header))
    for r in sorted(rows, key=lambda x: x["bufy_average"], reverse=True):
        row = [
            r["sid"], r["name"][:10], r["section_keys_count"], r["chars"],
            f"{r['ua']:.2f}", f"{r['ad']:.2f}", f"{r['ii']:.2f}", f"{r['es']:.2f}",
            f"{r['fid']:.2f}", f"{r['cov']:.2f}", f"{r['bufy_average']:.3f}", r["bufy_verdict"],
        ]
        print("\t".join(str(x) for x in row))

    with open("deliverables/bufy_scores_43.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    fields = ["id","sid","name","file","paper","section_keys_count","chars","ua","ad","ii","es","fid","cov","bufy_average","bufy_verdict","bufy_flags","bufy_note"]
    with open("deliverables/bufy_scores_43.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    c = Counter(r["bufy_verdict"] for r in rows)
    print("\nverdict 分布:", dict(c))
    print("ii<0.45 偏弱:", sum(1 for r in rows if r["ii"] < 0.45))
    print("4 段齐全:", sum(1 for r in rows if r["section_keys_count"] == 4), "/", len(rows))
    s = sorted(r["bufy_average"] for r in rows)
    print(f"average 中位数: {s[len(rows)//2]:.3f}")
    print(f"average 均值: {sum(s)/len(s):.3f}")
    print(f"average 范围: {min(s):.3f} ~ {max(s):.3f}")


if __name__ == "__main__":
    main()
