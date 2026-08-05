"""单元测试 _is_ocr_text_noise：验证质量门控能识别噪声、放行真实信号。

样本来自 1506.03340 / 1506.02690 诊断日志的真实 ocr_text。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.workers.figures import _is_ocr_text_noise

# === 噪声样本（应判 True）===
NOISE_SAMPLES = [
    # 1506.03340 图2：轴刻度碎片
    "Precision (0-100)\n0%\n10%\n20%\n30%\n40%\n50%\n60%\n70%\n80%\n90%\n100%",
    # 1506.03340 图4：CNN/DailyMail 新闻幻觉
    "by ent423 ,ent261 correspondent updated 9:49 pm et ,thu march 19,2015 (ent261) a ent114 was killed in a parachute accident in ent45 ,ent85 ,near ent312 ,a ent119 official told ent261 on wednesday .he",
    # 1506.03340 图10：模型幻觉
    'Figure Number: 164\nDescription of the right hand side: "X_UNK_:\\" this can not be called targeting"\nSource: https://www.example.com',
    # 1304.1574 图2：LaTeX 残渣
    "`\\multicolumn{4}{c | c| }{N_S}`",
    # 短文本
    "Method³ Acc %",
    # 空字符串
    "",
    "   ",
    # 1506.03340 图12-28 重复新闻（ent20/ent48 占位符密度高）
    "by ent20 ,ent48 correspondent updated 9:49 pm et ,thu march 19 ,2015 ( ent48 ) a ent69 was killed in a parachute accident in ent31 ,ent52 ,near ent49 ,a ent77 official told ent48 on wednesday .he was",
    # 1506.03340 图3：幻觉
    'The "ent23" is a data-based feature based on a series of data models, including a data-based feature with a "mom jeans" model, and a "in sight .ent164 and ent21" model.',
]

# === 真实信号样本（应判 False）===
SIGNAL_SAMPLES = [
    # 1506.02690 图1：真实表格 OCR
    "Table 2: Test accuracy of the best methods that utilize convolutional framework on CIFAR-10 dataset without data augmentation.\nMethod³ Acc %",
    # 正常图注
    "Figure 1: Domain Adaptation with Multiple Sources",
    # 正常图表描述
    "The chart shows the training loss decreasing from 2.5 to 0.3 over 100 epochs, with validation loss following a similar trajectory.",
    # 实验结果表
    "Model A: 92.3% accuracy on MNIST test set. Model B: 89.1% accuracy. Both models trained for 50 epochs with batch size 32.",
]

print("=== 噪声样本（应判 True）===")
passed_noise = 0
for i, s in enumerate(NOISE_SAMPLES, 1):
    result = _is_ocr_text_noise(s)
    status = "✓" if result else "✗ FAIL"
    print(f"  [{i}] {status} noise={result}  text[:60]={s[:60]!r}")
    if result:
        passed_noise += 1

print(f"\n噪声识别: {passed_noise}/{len(NOISE_SAMPLES)} 通过")

print("\n=== 真实信号样本（应判 False）===")
passed_signal = 0
for i, s in enumerate(SIGNAL_SAMPLES, 1):
    result = _is_ocr_text_noise(s)
    status = "✓" if not result else "✗ FAIL"
    print(f"  [{i}] {status} noise={result}  text[:60]={s[:60]!r}")
    if not result:
        passed_signal += 1

print(f"\n真实信号放行: {passed_signal}/{len(SIGNAL_SAMPLES)} 通过")

total_pass = passed_noise + passed_signal
total = len(NOISE_SAMPLES) + len(SIGNAL_SAMPLES)
print(f"\n=== 总体: {total_pass}/{total} 通过 ===")
if total_pass == total:
    print("✓ 质量门控逻辑正确")
else:
    print("✗ 需要调整规则")
